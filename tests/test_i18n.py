"""tools/i18n.sh: t() and normalize_answer(), which install.sh and uninstall.sh ask every question with."""
import unittest

from helpers import TOP, ShellTest

ARGS = ['A1', 'B2', 'C3', 'D4', 'E5', 'F6']


def translations(lang):
    out = {}
    for line in (TOP / 'i18n' / f'{lang}.tsv').read_text(encoding='utf-8').splitlines():
        if '\t' in line and not line.startswith('#'):
            k, v = line.split('\t', 1)
            out[k] = v
    return out


def expect(s):
    for i, a in enumerate(ARGS, 1):
        s = s.replace('{%d}' % i, a)
    return s


class Translate(ShellTest):
    def run_t(self, shell, lang, keys):
        """t KEY A1..A6 for every key, in one shell run; returns the outputs."""
        kf = self.tmp / 'keys'
        kf.write_text(''.join(k + '\n' for k in keys), encoding='utf-8')
        code = (f'TOP="{TOP}"; . "$TOP/tools/i18n.sh"; MU300_LANG={lang}; '
                f'while IFS= read -r k; do t "$k" {" ".join(ARGS)}; printf "\\036"; done < "{kf}"')
        r = self.sh(shell, code)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.split('\x1e')[:-1]

    def test_every_translation(self):
        for lang in ('tr', 'zh'):
            table = translations(lang)
            keys = sorted(table)
            for shell in self.each_shell():
                got = self.run_t(shell, lang, keys)
                self.assertEqual(len(got), len(keys))
                for k, g in zip(keys, got):
                    self.assertEqual(g, expect(table[k]), f'{lang}: {k}')

    def test_english_and_missing_messages_stay_english(self):
        keys = ['device: {1}', 'a message nobody translated {1} {2}', 'braces {x} and {7} stay {1}']
        for shell in self.each_shell():
            for lang in ('en', 'tr'):
                got = self.run_t(shell, lang, keys[1:] if lang == 'tr' else keys)
                want = [expect(k) for k in (keys[1:] if lang == 'tr' else keys)]
                self.assertEqual(got, want)

    def test_arguments_are_not_interpreted(self):
        # arguments come from the device (model names, paths): nothing in them may be expanded or break awk
        tricky = ['$HOME', '\\n', '%s', '"q"', "it's", 'a{1}b']
        for shell in self.each_shell():
            code = (f'TOP="{TOP}"; . "$TOP/tools/i18n.sh"; MU300_LANG=en; '
                    + '; '.join(f"t '<{{1}}>' '{a.replace(chr(39), chr(39) + chr(92) + chr(39) + chr(39))}'; echo"
                                for a in tricky))
            r = self.sh(shell, code)
            self.assertEqual(r.stdout.splitlines(), [f'<{a}>' for a in tricky])


class Answers(ShellTest):
    CASES = {'evet': 'yes', 'Evet': 'yes', 'y': 'yes', 'YES': 'yes', '是': 'yes', '好': 'yes',
             'hayır': 'no', 'hayir': 'no', 'n': 'no', '否': 'no', '不是': 'no',
             'güncelle': 'update', 'guncelle': 'update', '更新': 'update',
             'sil': 'wipe', '清除': 'wipe', '擦除': 'wipe',
             'INSTALL': 'INSTALL', 'overwrite': 'overwrite', '3': '3', '6.18': '6.18', '': ''}

    def test_normalize_answer(self):
        for shell in self.each_shell():
            code = f'TOP="{TOP}"; . "$TOP/tools/i18n.sh"; while IFS= read -r a; do normalize_answer "$a"; done'
            r = self.sh(shell, code, stdin=''.join(k + '\n' for k in self.CASES))
            self.assertEqual(r.stdout.split('\n')[:-1], list(self.CASES.values()))


if __name__ == '__main__':
    unittest.main()
