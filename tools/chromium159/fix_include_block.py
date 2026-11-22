"""Fix the include block of nova_core_config.cc for Chromium 159.

``base/json/json_value.h`` does not exist any more: the dictionary type
lives in ``base/values.h`` and the reader in ``base/json/json_reader.h``.
The angle-bracket includes are also wrong for a Chromium source file --
they are resolved relative to the source root, which works, but they are
not the style the tree uses and they hide the dependency from the
include-what-you-use checker.
"""

from pathlib import Path

PATH = Path(
    r"C:\Users\ezycloudx-admin\Desktop\NOVAPROFILE\novaprofile\core_patch\src\nova_core_config.cc"
)

OLD = '''#include <base/files/file_util.h>
#include <base/logging.h>
#include <base/path_service.h>

#include <memory>
#include <string>

#include "base/json/json_reader.h"
#include "base/json/json_value.h"
#include "url/gurl.h"
'''

NEW = '''#include <memory>
#include <optional>
#include <string>
#include <utility>
#include <vector>

#include "base/command_line.h"
#include "base/files/file_path.h"
#include "base/files/file_util.h"
#include "base/json/json_reader.h"
#include "base/logging.h"
#include "base/path_service.h"
#include "base/values.h"
#include "url/gurl.h"
'''

text = PATH.read_text(encoding="utf-8")
assert OLD in text, "the include block was not found"
PATH.write_text(text.replace(OLD, NEW, 1), encoding="utf-8", newline="")
print("includes rewritten")
