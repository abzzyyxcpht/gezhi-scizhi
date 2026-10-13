import unittest

from scripts.gezhi import parse_args


class LauncherTest(unittest.TestCase):
    def test_parse_demo_with_topic_and_output(self):
        args = parse_args(["demo", "科研智能体", "--out", "tmp-out"])
        self.assertEqual(args.command, "demo")
        self.assertEqual(args.topic, "科研智能体")
        self.assertEqual(args.out, "tmp-out")
        self.assertFalse(args.online)

    def test_unknown_command_is_rejected(self):
        with self.assertRaises(SystemExit):
            parse_args(["unknown"])
