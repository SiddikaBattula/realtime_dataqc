

import pymysql
import os
from dotenv import load_dotenv

load_dotenv()
# table name :- timebaselastrecord
conn = pymysql.connect(
    host="43.241.39.241",
    user=os.getenv("DB_USERNAME"),
    password=os.getenv("DB_PASSWORD"),
    port=int(os.getenv("DB_PORT")),
    database="dk-1176",           #"mndwo181hdb_1(m",
    charset="utf8",
)

print("Connected Successfully")

cursor = conn.cursor()


# cursor.execute("SELECT * from dataqcalert")
# cursor.execute("DELETE FROM dataqcalert")
# cursor.execute("SELECT * FROM drilling WHERE TOT_DPT_MD = 1445.5")
# cursor.execute("SHOW TABLES FROM `dk-1140-1-wc`;")
cursor.execute("SHOW COLUMNS FROM timebaselastrecord")
# cursor.execute("SELECT * FROM drilling LIMIT 5")
# cursor.execute("""
#     SELECT *
#     FROM drilling
#     ORDER BY TIME DESC
#     LIMIT 10
# """)

# cursor.execute("SELECT TOT_DPT_MD from drilling")
# cursor.execute("SELECT ROP,HOOKLOAD_AVG,WOB,ROT_SPEED,MP1_SPM,MP2_SPM,MP3_SPM,STP_PRS_AVG,Total_Gas,TOT_ALKANE,DENS_IN,DENS_OUT,TEMP_IN,TEMP_OUT,H2S1,LEL,Co2_1,FLOW_IN,PitSumVol1,RETURNS,ROT_TORQUE_AVG,CONDUCT_IN,CONDUCT_OUT FROM drilling WHERE TOT_DPT_MD=1484.0")


# cursor.execute("SELECT TOT_DPT_MD FROM drilling WHERE TOT_DPT_MD BETWEEN 1484.00 AND 1512")
# cursor.execute("SELECT Rdtime,TOT_DPT_MD,ROP FROM drilling WHERE TOT_DPT_MD BETWEEN 1257.0 AND 1260.0")



rows = cursor.fetchall()


for row in rows:
    print(row)


conn.close()