import sqlite3

db = r"C:\Users\ezycloudx-admin\AppData\Roaming\NOVA PROFILE\data\state.db"
con = sqlite3.connect("file:" + db + "?mode=ro", uri=True)
for (name,) in con.execute(
    "select name from sqlite_master where type='table'"
):
    print("TABLE", name)
    cols = [r[1] for r in con.execute(f"pragma table_info({name})")]
    print("   cols:", cols)
    try:
        rows = list(con.execute(f"select * from {name} limit 5"))
        for row in rows:
            print("   ", row)
    except Exception as exc:
        print("   err:", exc)
