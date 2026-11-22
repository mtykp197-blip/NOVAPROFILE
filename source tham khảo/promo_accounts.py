"""Account lists for the promo signup, kept as plain text files next to NOVA.

Two files live in the NOVA data folder: one for the accounts whose promo was
granted and one for the accounts whose promo was refused. A third file keeps
the messages of the sites that did not fit either list. Every file holds one
section per site, so the user can read, edit or delete lines by hand.
"""
import os
import re
import secrets
import threading
from pathlib import Path

# The sites of the promo signup, with the name of their section.
SITE_SECTIONS = {
    'cm': 'cm',
    'sc': 'sc',
    'c168': 'c168',
    '78win': '78win',
    'jun': 'jun',
    'on68': 'on68',
}
# The order of the sections in a file.
SITE_ORDER = ('cm', 'sc', 'c168', '78win', 'jun', 'on68')
# One account line is: user|password|name|withdraw_password|phone|number|bank
LINE_PATTERN = re.compile(r'^[^|\r\n]{1,200}(\|[^|\r\n]{0,200}){6}[^\r\n]{0,200}$')


def _atomic_text(path, text):
    """Write a text file in one step, so a crash never leaves half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + secrets.token_hex(4) + '.tmp')
    temporary.write_text(text, encoding='utf8')
    os.replace(temporary, path)


class PromoAccounts:
    """Read and write the account lists of the promo signup."""

    def __init__(self, root):
        self.root = Path(root).resolve()
        self.folder = self.root / 'promo-accounts'
        self.success = self.folder / 'promo-success.txt'
        self.failed = self.folder / 'promo-failed.txt'
        self.messages = self.folder / 'promo-messages.txt'
        self.lock = threading.RLock()

    def _path(self, kind):
        if kind == 'success':
            return self.success
        if kind == 'failed':
            return self.failed
        if kind == 'messages':
            return self.messages
        raise ValueError('Loai danh sach khong hop le.')

    def _read_sections(self, path):
        """Reads a file into its sections. The name of a section is its header."""
        sections = {site: [] for site in SITE_ORDER}
        if not path.exists():
            return sections
        current = None
        for raw in path.read_text(encoding='utf8', errors='replace').splitlines():
            line = raw.strip()
            if not line:
                continue
            if line in SITE_SECTIONS.values():
                current = line
                continue
            if current is None:
                continue
            sections[current] = sections.get(current, []) + [line]
        return sections

    def _write_sections(self, path, sections):
        lines = []
        for site in SITE_ORDER:
            lines.append(SITE_SECTIONS[site])
            lines.extend(sections.get(site, []))
        _atomic_text(path, ''.join(line + '\n' for line in lines))

    def list_all(self):
        """Every account of every site, grouped by site and kind."""
        with self.lock:
            success = self._read_sections(self.success)
            failed = self._read_sections(self.failed)
            return {
                site: {'success': success.get(site, []), 'failed': failed.get(site, [])}
                for site in SITE_ORDER
            }

    def add(self, site, kind, line, message=''):
        """Add one account. A line that is already there is not added twice."""
        line = str(line or '').strip()
        if not line or len(line) > 2000:
            raise ValueError('Dong tai khoan khong hop le.')
        if not LINE_PATTERN.match(line):
            raise ValueError('Dong tai khoan sai dinh dang: user|matkhau|ten|matkhau-rut|sdt|stk|nganhang')
        if kind not in ('success', 'failed'):
            raise ValueError('Loai danh sach khong hop le.')
        if site not in SITE_ORDER:
            raise ValueError('Khong co danh sach cho web nay.')
        with self.lock:
            path = self._path(kind)
            sections = self._read_sections(path)
            if line in sections[site]:
                return {'added': False, 'total': len(sections[site]), 'saved': True}
            sections[site].append(line)
            self._write_sections(path, sections)
            # The line is read back from the file, so the caller knows the
            # account really sits on the disk and not only in the memory.
            check = self._read_sections(path)
            if line not in check.get(site, []):
                raise ValueError('Khong ghi duoc tai khoan vao danh sach.')
            return {'added': True, 'total': len(check[site]), 'saved': True}

    def remove(self, site, kind, line):
        """Drop one account from a list."""
        if site not in SITE_ORDER:
            raise ValueError('Khong co danh sach cho web nay.')
        with self.lock:
            path = self._path(kind)
            sections = self._read_sections(path)
            if line not in sections.get(site, []):
                return {'removed': False, 'total': len(sections.get(site, []))}
            sections[site].remove(line)
            self._write_sections(path, sections)
            return {'removed': True, 'total': len(sections[site])}

    def add_message(self, site, line, message):
        """Keep a message of a site that did not fit the other lists."""
        site = str(site or '').strip()
        message = str(message or '').strip()
        if not message:
            raise ValueError('Thong bao rong.')
        entry = f'{site}|{line}|{message}'
        with self.lock:
            messages = self._read_sections(self.messages)
            messages.setdefault(site, []).append(entry)
            self._write_sections(self.messages, messages)
            return {'added': True}

    def clear(self, site, kind):
        """Empty one list of one site."""
        if site not in SITE_ORDER:
            raise ValueError('Khong co danh sach cho web nay.')
        with self.lock:
            path = self._path(kind)
            sections = self._read_sections(path)
            total = len(sections.get(site, []))
            sections[site] = []
            self._write_sections(path, sections)
            return {'cleared': total, 'total': 0}

    def paths(self):
        """Where the files are, so NOVA can show them to the user."""
        return {
            'folder': str(self.folder),
            'success': str(self.success),
            'failed': str(self.failed),
            'messages': str(self.messages),
        }
