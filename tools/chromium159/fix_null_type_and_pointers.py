"""The last two Chromium 159 fixes for nova_core_config.cc.

* ``base::Value`` lost its NULL type: the enum now ends at ``LIST`` and a
  JSON ``null`` parses to ``Type::NONE``, so ``is_null()`` became
  ``is_none()``.

* The helpers take the dictionary by reference, so the pointers that
  ``FindDict()`` returned have to be dereferenced at the call site.
"""

import re
from pathlib import Path

PATH = Path(
    r"C:\Users\ezycloudx-admin\Desktop\NOVAPROFILE\novaprofile\core_patch\src\nova_core_config.cc"
)

text = PATH.read_text(encoding="utf-8")

# --------------------------------------------------------------------------
# 1.  The null test.
# --------------------------------------------------------------------------
text = text.replace("value->is_null()", "value->is_none()")

# --------------------------------------------------------------------------
# 2.  The pointers that the readers need by reference.
# --------------------------------------------------------------------------
names = sorted(set(re.findall(r"const base::DictValue\* (\w+) = FindDict", text)))
for name in names:
    text = re.sub(
        rf"\b(Read\w+|Parse\w+|read_\w+)\(\s*{re.escape(name)}\s*,",
        rf"\1(*{name},",
        text,
    )

PATH.write_text(text, encoding="utf-8", newline="")
print("rewrote", PATH)
print("is_none():", text.count("is_none()"))
print("pointer call sites still bare:")
for line in text.splitlines():
    stripped = line.strip()
    for name in names:
        if re.search(
            rf"\b(Read\w+|Parse\w+|read_\w+)\(\s*{re.escape(name)}\s*,", stripped
        ):
            print("   ", stripped)
