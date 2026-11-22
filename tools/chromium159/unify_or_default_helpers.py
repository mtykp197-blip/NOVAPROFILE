"""Unify the ``*OrDefault`` helpers with the strict readers.

The strict readers all have the shape ``(dict, key, out, error)``: they
report a missing or misshaped field as an error.  The ``*OrDefault``
helpers had a different shape -- ``(dict, key, fallback, out)`` -- which
made the two families easy to mix up: a call site that passed ``out``
where the fallback belonged compiled against one signature and failed
against the other.

The unified shape is ``(dict, key, out, error)``.  A field that is absent
or has the wrong shape leaves ``*out`` alone, so the caller seeds the
field with the value it wants before the call.  The field's own default
in the configuration structure is that value.
"""

import re
from pathlib import Path

PATH = Path(
    r"C:\Users\ezycloudx-admin\Desktop\NOVAPROFILE\novaprofile\core_patch\src\nova_core_config.cc"
)

text = PATH.read_text(encoding="utf-8")

# --------------------------------------------------------------------------
# 1.  The three helpers.
# --------------------------------------------------------------------------
OLD_STRING = '''bool ReadStringOrDefault(const base::DictValue& dict,
                          const char* key,
                          const std::string& fallback,
                          std::string* out) {
  const base::Value* value = dict.Find(key);
  if (!value || !value->is_string()) {
    *out = fallback;
    return true;
  }
  *out = value->GetString();
  return true;
}'''
NEW_STRING = '''// A missing or misshaped field leaves |out| alone, so the caller seeds it
// with the value it wants to fall back to.  The signature matches the
// strict readers above.
bool ReadStringOrDefault(const base::DictValue& dict,
                          const char* key,
                          std::string* out,
                          std::string* error) {
  const base::Value* value = dict.Find(key);
  if (!value) {
    return true;
  }
  if (!value->is_string()) {
    *error = std::string("non-string field: ") + key;
    return false;
  }
  *out = value->GetString();
  return true;
}'''
assert OLD_STRING in text, "ReadStringOrDefault was not found"
text = text.replace(OLD_STRING, NEW_STRING, 1)

OLD_INT = '''bool ReadIntOrDefault(const base::DictValue& dict,
                       const char* key,
                       int fallback,
                       int* out) {
  const base::Value* value = dict.Find(key);
  if (!value || !value->is_int()) {
    *out = fallback;
    return true;
  }
  *out = value->GetInt();
  return true;
}'''
NEW_INT = '''bool ReadIntOrDefault(const base::DictValue& dict,
                       const char* key,
                       int* out,
                       std::string* error) {
  const base::Value* value = dict.Find(key);
  if (!value) {
    return true;
  }
  if (!value->is_int()) {
    *error = std::string("non-integer field: ") + key;
    return false;
  }
  *out = value->GetInt();
  return true;
}'''
assert OLD_INT in text, "ReadIntOrDefault was not found"
text = text.replace(OLD_INT, NEW_INT, 1)

OLD_BOOL = '''bool ReadBoolOrDefault(const base::DictValue& dict,
                       const char* key,
                       bool fallback,
                       bool* out) {
  const base::Value* value = dict.Find(key);
  if (!value || !value->is_bool()) {
    *out = fallback;
    return true;
  }
  *out = value->GetBool();
  return true;
}'''
NEW_BOOL = '''bool ReadBoolOrDefault(const base::DictValue& dict,
                       const char* key,
                       bool* out,
                       std::string* error) {
  const base::Value* value = dict.Find(key);
  if (!value) {
    return true;
  }
  if (!value->is_bool()) {
    *error = std::string("non-boolean field: ") + key;
    return false;
  }
  *out = value->GetBool();
  return true;
}'''
assert OLD_BOOL in text, "ReadBoolOrDefault was not found"
text = text.replace(OLD_BOOL, NEW_BOOL, 1)

# --------------------------------------------------------------------------
# 2.  The call sites that passed the fallback explicitly.
# --------------------------------------------------------------------------
EXPLICIT = [
    # (old call, new call)
    ('ReadStringOrDefault(dict, "salt", "", &out->salt);',
     'ReadStringOrDefault(dict, "salt", &out->salt, error);'),
    ('ReadStringOrDefault(item_dict, "facing_mode", "", &entry.facing_mode);',
     'ReadStringOrDefault(item_dict, "facing_mode", &entry.facing_mode, error);'),
    ('ReadStringOrDefault(voice_dict, "voice_uri", "", &voice.voice_uri);',
     'ReadStringOrDefault(voice_dict, "voice_uri", &voice.voice_uri, error);'),
    ('ReadBoolOrDefault(voice_dict, "default", false, &voice.is_default);',
     'ReadBoolOrDefault(voice_dict, "default", &voice.is_default, error);'),
    ('ReadBoolOrDefault(voice_dict, "local_service", true, &voice.local_service);',
     'ReadBoolOrDefault(voice_dict, "local_service", &voice.local_service, error);'),
]
for old, new in EXPLICIT:
    assert old in text, f"call site not found: {old}"
    text = text.replace(old, new)

# The speech-voice loop reads into a local, so the local has to be seeded
# with the fallback before the call.
text = text.replace(
    '''    SpeechVoice voice;
    const base::DictValue& voice_dict = item.GetDict();''',
    '''    SpeechVoice voice;
    voice.is_default = false;
    voice.local_service = true;
    const base::DictValue& voice_dict = item.GetDict();''',
    1,
)

PATH.write_text(text, encoding="utf-8", newline="")
print("helpers unified")
print("remaining OrDefault calls:")
for line in text.splitlines():
    if "OrDefault(" in line and "bool Read" not in line:
        print("   ", line.strip())
