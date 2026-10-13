const secretKey = key => /api.?key|secret|password|passwd|authorization|credential|private.?key|cookie/i.test(key)
  || /^(?:access|refresh|auth|id)?tokens?$/i.test(key.replace(/[_-]/g, ''));
const measurement = key => /(?:^|_)tokens$|token_?count$|token_?budget$/i.test(key);

export function redactText(value, knownSecrets = []) {
  let text = String(value ?? '');
  for (const secret of knownSecrets) if (secret) text = text.split(secret).join('[已隐藏]');
  return text
    .replace(/\b(?:Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+/gi, 'Bearer [已隐藏]')
    .replace(/((?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|client[_ -]?secret|password|passwd|authorization|credential|secret|\btoken)\s*["']?\s*[:=]\s*)(?:"(?:[^"\\]|\\.)*"|'[^']*'|[^\s,;}]+)/gi, '$1[已隐藏]')
    .replace(/(https?:\/\/)[^/\s@]+@/gi, '$1[已隐藏]@');
}

export function credentialValues(value) {
  if (!value || typeof value !== 'object') return [];
  return Object.entries(value).flatMap(([key, item]) =>
    secretKey(key) && typeof item === 'string' ? [item] : credentialValues(item));
}

export function redactSettings(value) {
  if (Array.isArray(value)) return value.map(redactSettings);
  if (typeof value === 'string') return redactText(value);
  if (!value || typeof value !== 'object') return value;
  return Object.fromEntries(Object.entries(value).map(([key, item]) => {
    const count = measurement(key) && (typeof item === 'number' || item == null);
    const configured = /^(has_api_key|api_key_configured|api_key_set|key_configured)$/i.test(key) && typeof item === 'boolean';
    return [key, secretKey(key) && !count && !configured ? (item ? '[已设置，隐藏]' : '未设置') : redactSettings(item)];
  }));
}

export function safeCommandEcho(raw) {
  const text = String(raw).trim();
  if (/^\/connect(?:\s|$)/i.test(text)) return '/connect [地址与令牌已隐藏]';
  if (/^\/model\s+key(?:\s|$)/i.test(text)) return '/model key [已隐藏]';
  const jsonCommand = text.match(/^\/(connection|profile)\s+save(?:\s|$)/i);
  if (jsonCommand) return `/${jsonCommand[1].toLowerCase()} save [配置内容已隐藏]`;
  if (/^\/mcp-config\s+save(?:\s|$)/i.test(text)) return '/mcp-config save [配置内容已隐藏]';
  const controlCall = text.match(/^\/(browser|computer)\s+call(?:\s|$)/i);
  if (controlCall) return `/${controlCall[1].toLowerCase()} call [工具参数已隐藏]`;
  return redactText(text);
}
