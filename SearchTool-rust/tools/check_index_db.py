import os
import sqlite3
import time


def main():
    p = os.path.join(os.environ.get("USERPROFILE", ""), ".filesearch", "index.db")
    print("db:", p)
    if not os.path.exists(p):
        print("NOT FOUND")
        return

    con = sqlite3.connect(p)
    cur = con.cursor()

    def q(name, sql, args=()):
        try:
            cur.execute(sql, args)
            rows = cur.fetchall()
            print(name + ":", rows[:10])
        except Exception as e:
            print("ERR", name, e)

    q("total", "select count(*) from files")
    q("mtime_nonzero", "select count(*) from files where mtime>0")
    q("mtime_max", "select max(mtime) from files")
    q("mtime_min_nz", "select min(mtime) from files where mtime>0")
    q("ext_pdf", "select count(*) from files where extension='.pdf'")
    q("ext_like_pdf", "select count(*) from files where extension like '%pdf%'")
    q("ext_top", "select extension,count(*) c from files group by extension order by c desc limit 10")

    now = time.time()
    cut = now - 7 * 86400
    q("dm7d_count", "select count(*) from files where mtime>=?", (cut,))
    q("pdf_sample", "select full_path,mtime,extension from files where extension='.pdf' order by mtime desc limit 5")

    con.close()


if __name__ == "__main__":
    main()


