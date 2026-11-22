"""Port nova_core_config.cc from ``base::Value::Dict`` to ``base::DictValue``.

Chromium 159 split the dictionary out of ``base::Value``: the nested type
``base::Value::Dict`` is gone and the standalone class ``base::DictValue``
took its place.  A ``base::DictValue`` *is* the dictionary, so it has no
``GetDict()`` method of its own -- the ``GetDict()`` accessor still lives
on ``base::Value`` and hands out a reference to the dictionary it holds.

The port therefore does three things:

* ``base::Value::Dict`` becomes ``base::DictValue``,
* ``value->GetDict()`` -- where ``value`` is a ``base::Value`` -- stays as
  it is, because ``base::Value::GetDict()`` still exists,
* ``name->GetDict()`` -- where ``name`` is a ``const base::DictValue*``
  returned by ``FindDict()`` -- becomes ``*name``, because a
  ``base::DictValue`` is already the dictionary.

The seed epoch and the nudge step are ``uint32_t`` while the rest of the
configuration is signed, so ``ReadInt`` needs an unsigned overload as
well.
"""

import re
from pathlib import Path

PATH = Path(
    r"C:\Users\ezycloudx-admin\Desktop\NOVAPROFILE\novaprofile\core_patch\src\nova_core_config.cc"
)

text = PATH.read_text(encoding="utf-8")

# --------------------------------------------------------------------------
# 1.  The type rename.
# --------------------------------------------------------------------------
text = text.replace("base::Value::Dict", "base::DictValue")

# --------------------------------------------------------------------------
# 2.  The dictionaries that FindDict() returned are already dictionaries.
# --------------------------------------------------------------------------
names = sorted(set(re.findall(r"const base::DictValue\* (\w+) = FindDict", text)))
for name in names:
    text = text.replace(f"{name}->GetDict()", f"*{name}")

# A reference bound to the dictionary a pointer points at.
text = re.sub(
    r"const base::DictValue& (\w+) = (\w+);",
    r"const base::DictValue& \1 = *\2;",
    text,
)

# --------------------------------------------------------------------------
# 3.  The unsigned fields.
# --------------------------------------------------------------------------
anchor = "bool ReadIntOrDefault(const base::DictValue& dict,"
uint32_overload = """// The seed epoch and the nudge step are unsigned, so they need their own
// overload: an ``int*`` cannot bind to a ``uint32_t*``.
bool ReadInt(const base::DictValue& dict,
             const char* key,
             uint32_t* out,
             std::string* error) {
  const base::Value* value = dict.Find(key);
  if (!value || !value->is_int()) {
    *error = std::string("missing or non-integer field: ") + key;
    return false;
  }
  *out = static_cast<uint32_t>(value->GetInt());
  return true;
}

"""
assert anchor in text, "the ReadIntOrDefault anchor was not found"
text = text.replace(anchor, uint32_overload + anchor, 1)

# --------------------------------------------------------------------------
# 4.  The JSON reader and the root value.
# --------------------------------------------------------------------------
text = text.replace(
    "base::JSONReader::Read(json, base::JSON_PARSE_RFC)",
    "base::JSONReader::Read(json, base::JSON_PARSE_RFC)",
)

PATH.write_text(text, encoding="utf-8", newline="")
print("rewrote", PATH)
print("FindDict variables rewritten:", len(names))
print("remaining '->GetDict()' on pointers:", sorted(set(re.findall(r"\w+->GetDict\(\)", text))))
