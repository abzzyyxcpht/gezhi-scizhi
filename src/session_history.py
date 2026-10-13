"""Rebuildable, redacted SQLite display index; never an execution authority."""
from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import re
import sqlite3
import uuid

from .session_lock import session_lock
from . import session_events
from .workbench import redact

_ID = re.compile(r'[A-Za-z0-9_-]{1,128}\Z')
_VERSION = 2
_DISPLAY_LIMIT = 20000
_KINDS = {'user_message', 'assistant_message', 'steer_applied'}


def _identifier(sid):
    if not isinstance(sid, str) or not _ID.fullmatch(sid):
        raise ValueError('会话 ID 无效')
    return sid


def index_path(store, sid):
    root = store.root.resolve()
    target = root / '.history-index' / f'{_identifier(sid)}.sqlite'
    resolved = target.resolve()
    def comparable(path):
        text = str(path)
        # While SQLite holds the new file, Windows realpath can retain the
        # extended-length prefix for the target but strip it from its parent.
        if os.name == 'nt':
            if text.startswith('\\\\?\\UNC\\'):
                text = '\\\\' + text[8:]
            elif text.startswith('\\\\?\\'):
                text = text[4:]
        return type(path)(text)
    if not comparable(resolved).is_relative_to(comparable(root)):
        raise ValueError('历史索引目录超出会话数据范围')
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def _decode(line):
    event = session_events._decode_event(line)
    if not event or event.get('type') not in _KINDS or not isinstance(event.get('payload'), dict):
        return None
    payload, kind = event['payload'], event['type']
    content = payload.get('content') if kind == 'assistant_message' else payload.get('text')
    text = str(redact(str(content or '')))
    return ('assistant' if kind == 'assistant_message' else 'user', text, text.casefold(),
            str(event.get('timestamp') or ''), kind)


def _connect(path):
    connection = sqlite3.connect(path, timeout=5)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute('PRAGMA busy_timeout=5000')
        version = connection.execute('PRAGMA user_version').fetchone()[0]
        if version != _VERSION:
            connection.executescript('DROP TABLE IF EXISTS messages; DROP TABLE IF EXISTS search; DROP TABLE IF EXISTS meta;')
        connection.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        connection.execute('CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY, role TEXT NOT NULL, content TEXT NOT NULL, folded TEXT NOT NULL, timestamp TEXT NOT NULL, kind TEXT NOT NULL)')
        try:
            connection.execute("CREATE VIRTUAL TABLE IF NOT EXISTS search USING fts5(folded, content='messages', content_rowid='id', tokenize='trigram case_sensitive 1')")
            search = True
        except sqlite3.OperationalError:
            search = False
        connection.execute(f'PRAGMA user_version={_VERSION}')
        connection.commit()
        return connection, search
    except Exception:
        connection.close()
        raise


def _sync(connection, search, log, sid):
    metadata = {row['key']: row['value'] for row in connection.execute('SELECT key,value FROM meta')}
    previous = session_events._decode_cursor(metadata.get('cursor'))
    generation = metadata.get('generation')
    with log.open('rb') as stream:
        signature = session_events._signature(os.fstat(stream.fileno()))
        backend = 'trigram' if search else 'literal'
        valid = (previous is not None and metadata.get('search_backend') == backend
                 and session_events._matches(stream, previous, sid, signature))
        if valid and previous.signature == signature and previous.drained:
            return generation, False, 0, signature
        rebuilt = not valid
        if rebuilt:
            if search:
                connection.execute("INSERT INTO search(search) VALUES('delete-all')")
            connection.execute('DELETE FROM messages')
            generation = uuid.uuid4().hex
        offset = previous.offset if valid else 0
        decoded = 0
        if not valid or previous.signature != signature or not previous.drained:
            stream.seek(offset)
            while stream.tell() < signature[2]:
                line = stream.readline(signature[2] - stream.tell())
                if not line.endswith(b'\n'):
                    break
                offset = stream.tell()
                row = _decode(line)
                decoded += 1
                if row is not None:
                    message = connection.execute('INSERT INTO messages(role,content,folded,timestamp,kind) VALUES(?,?,?,?,?)', row)
                    if search:
                        connection.execute('INSERT INTO search(rowid,folded) VALUES(?,?)', (message.lastrowid, row[2]))
        head_size = min(signature[2], session_events._ANCHOR_BYTES)
        head, anchor = session_events._fingerprints(stream, head_size, offset)
        cursor = session_events._Cursor(sid, signature, offset, head_size, head, anchor, True)
    after = session_events._signature(log.stat())
    if (after[:2] != signature[:2] or after[2] < signature[2]
            or after[2] == signature[2] and after[3] != signature[3]):
        raise OSError('历史记录正在改写，请重试')
    connection.executemany('INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)',
                          [('cursor', session_events._encode_cursor(cursor)), ('generation', generation), ('search_backend', backend)])
    return generation, rebuilt, decoded, signature


def _snapshot(value, sid):
    if value is None:
        return None
    if not isinstance(value, str) or not 1 <= len(value) <= 1024:
        raise ValueError('历史快照标识无效')
    try:
        row = json.loads(base64.b64decode(value + '=' * (-len(value) % 4), altchars=b'-_', validate=True))
        if (not isinstance(row, list) or len(row) != 4 or row[0] != sid
                or not isinstance(row[1], str) or not re.fullmatch(r'[0-9a-f]{32}', row[1])
                or type(row[2]) is not int or row[2] < 0 or not isinstance(row[3], str)):
            raise ValueError
        return row
    except (ValueError, TypeError, UnicodeError) as error:
        raise ValueError('历史快照标识无效或属于其他会话') from error


def history(session_id, *, store=None, query='', page=None, page_size=40, snapshot=None):
    from .agent_runtime import SessionStore
    from . import config
    sid = _identifier(session_id)
    if not isinstance(query, str) or len(query) > 1000 or '\x00' in query:
        raise ValueError('历史搜索关键词须为不超过1000字符的文本')
    if type(page_size) is not int or not 1 <= page_size <= 100 or (page is not None and (type(page) is not int or not 1 <= page <= 10**9)):
        raise ValueError('历史页码或每页条数无效')
    store = store or SessionStore(config.DATA_DIR / 'sessions')
    log, target = store._path(sid), index_path(store, sid)
    if not log.is_file():
        raise ValueError('会话不存在')
    previous = _snapshot(snapshot, sid)
    needle = query.strip().casefold()
    query_hash = hashlib.sha256(needle.encode()).hexdigest()
    if previous and previous[3] != query_hash:
        raise ValueError('搜索条件已变化，请使用新的历史快照')
    with session_lock(target):
        corrupt = False
        for attempt in range(2):
            connection = None
            try:
                connection, search = _connect(target)
                with connection:
                    generation, rebuilt, decoded, signature = _sync(connection, search, log, sid)
                    maximum = connection.execute('SELECT COALESCE(MAX(id),0) FROM messages').fetchone()[0]
                    reset = bool(previous and (previous[1] != generation or previous[2] > maximum))
                    ceiling = previous[2] if previous and not reset else maximum
                    where, parameters = 'id <= ?', [ceiling]
                    accelerated = search and len(needle) >= 3
                    if needle:
                        where += ' AND instr(folded,?) > 0'
                        parameters.append(needle)
                        if accelerated:
                            where += ' AND id IN (SELECT rowid FROM search WHERE search MATCH ?)'
                            parameters.append('"' + needle.replace('"', '""') + '"')
                    total = connection.execute('SELECT COUNT(*) FROM messages WHERE ' + where, parameters).fetchone()[0]
                    pages = max(1, math.ceil(total / page_size))
                    number = min(page or (1 if needle else pages), pages)
                    offset = (number - 1) * page_size
                    rows = connection.execute('SELECT id,role,substr(content,1,?) AS content,length(content) AS characters,timestamp,kind '
                        'FROM messages WHERE ' + where + ' ORDER BY id LIMIT ? OFFSET ?',
                        [_DISPLAY_LIMIT, *parameters, page_size, offset]).fetchall()
                    messages = [{**dict(row), 'truncated': row['characters'] > _DISPLAY_LIMIT} for row in rows]
                token = base64.urlsafe_b64encode(json.dumps([sid, generation, ceiling, query_hash]).encode()).decode().rstrip('=')
                return {'session_id': sid, 'query': query.strip(), 'messages': messages, 'page': number,
                        'pages': pages, 'page_size': page_size, 'total': total, 'snapshot': token,
                        'reset': reset, 'new_messages': maximum > ceiling, 'index_rebuilt': rebuilt or corrupt,
                        'decoded_events': decoded, 'search_method': 'trigram_literal' if accelerated else 'literal_scan' if needle else 'indexed_page',
                        'display_limit': _DISPLAY_LIMIT, 'generation': generation, 'journal_bytes': signature[2]}
            except sqlite3.DatabaseError as error:
                if getattr(error, 'sqlite_errorcode', None) not in {sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB} or attempt:
                    raise OSError('历史索引暂不可用，请稍后重试；原始会话未更改') from error
                if connection:
                    connection.close()
                    connection = None
                target.unlink(missing_ok=True)  # Derived, session-scoped cache only.
                corrupt = True
            finally:
                if connection:
                    connection.close()
    raise OSError('历史索引重建失败')


def message(session_id, message_id, *, store=None, generation, offset=0, limit=20000):
    """Explicit chunks of a long message; old-generation IDs cannot address new data."""
    from .agent_runtime import SessionStore
    from . import config
    if type(message_id) is not int or message_id < 1 or type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 20000:
        raise ValueError('消息序号或文本分页无效')
    store = store or SessionStore(config.DATA_DIR / 'sessions')
    state = history(session_id, store=store, page_size=1)
    target = index_path(store, session_id)
    with session_lock(target):
        connection, _ = _connect(target)
        try:
            actual = connection.execute("SELECT value FROM meta WHERE key='generation'").fetchone()[0]
            if generation != actual or state['generation'] != actual:
                raise ValueError('历史记录已改写，请刷新后重新打开消息')
            row = connection.execute('SELECT role,substr(content,?,?) AS content,length(content) AS characters,timestamp,kind FROM messages WHERE id=?',
                                     (offset + 1, limit, message_id)).fetchone()
            if not row or offset > row['characters']:
                raise ValueError('消息或文本位置不存在')
            end = min(offset + limit, row['characters'])
            return {'session_id': session_id, 'message_id': message_id, 'generation': actual, **dict(row),
                    'offset': offset, 'next_offset': end if end < row['characters'] else None}
        finally:
            connection.close()


def handle(req):
    try:
        cmd = req.get('cmd')
        allowed = {'cmd', 'session_id', 'query', 'page', 'page_size', 'snapshot'} if cmd == 'session_history' else {
            'cmd', 'session_id', 'message_id', 'generation', 'offset', 'limit'}
        if cmd not in {'session_history', 'session_history_message'} or set(req) - allowed:
            raise ValueError('历史请求包含无效字段')
        options = {key: value for key, value in req.items() if key not in {'cmd', 'session_id'}}
        value = history(req.get('session_id'), **options) if cmd == 'session_history' else message(req.get('session_id'), **options)
        return {'ok': True, 'history' if cmd == 'session_history' else 'message': value}
    except (OSError, ValueError, KeyError, TypeError) as error:
        return {'ok': False, 'error': redact(str(error))}
