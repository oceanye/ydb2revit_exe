# This is a sample Python script.
# coding:utf-8

import tinker
# Press Shift+F10 to execute it or replace it with your code.
# Press Double Shift to search everywhere for classes, files, tool windows, actions, and settings.
import os


def print_hi(name):
    # Use a breakpoint in the code line below to debug your script.
    print(f'Hi, {name}')  # Press Ctrl+F8 to toggle the breakpoint.


# Press the green button in the gutter to run the script.


# See PyCharm help at https://www.jetbrains.com/help/pycharm/
import sqlite3
import tkinter as tk
from tkinter import filedialog

if __name__ == '__main__':
    print_hi('PyCharm')



root = tk.Tk()
root.withdraw()

#Folderpath = filedialog.askdirectory()
Filepath=filedialog.askopenfilenames()


grid = []
bsect = []
bstdflr = []
gridid = []
bjt1 = []
bjt2 = []
jtid = []
bjtx = []
bjty = []
bstartx = []
bstarty = []
stdflrid = []
levelb1 = []
levelb = []
height = []
bstartz = []
bendx = []
bendy = []
bendz = []
bsectid = []
bsectinfo = []
bsection = []
cstdflr = []
cjt = []
csect = []
csectid = []
csectinfo = []
cstartx = []
cstarty = []
cstartz = []
cendz = []
csection = []
spbid = []
spbshape = []
spbname = []
bid = []
cid = []
hd1 = []
hd2 = []
ceccx=[]
ceccy=[]
crot=[]



s = list(Filepath)
s="".join(tuple(s))
print(s)

cnR = sqlite3.connect(s)

print("Opened database successfully")

c = cnR.cursor()
cursor1 = c.execute("SELECT GridID,SectID,StdFlrID,ID,HDiff1,HDiff2 from tblBeamSeg")
for row in cursor1:
    grid.append(row[0])
    bsect.append(row[1])
    bstdflr.append(row[2])
    bid.append(row[3])
    hd1.append(row[4])
    hd2.append(row[5])
cnR.commit()

c = cnR.cursor()
cursor2 = c.execute("SELECT ID,Jt1ID,Jt2ID from tblGrid")
for row in cursor2:
    gridid.append(row[0])
    bjt1.append(row[1])
    bjt2.append(row[2])
cnR.commit()

jhdiff = []
c = cnR.cursor()
cursor3 = c.execute("SELECT ID,X,Y,HDiff from tblJoint")
for row in cursor3:
    jtid.append(row[0])
    bjtx.append(row[1])
    bjty.append(row[2])
    jhdiff.append(row[3])
cnR.commit()

c = cnR.cursor()
cursor4 = c.execute("SELECT StdFlrID,LevelB,Height from tblFloor")
for row in cursor4:
    stdflrid.append(row[0])
    levelb.append(row[1])
    height.append(row[2])
cnR.commit()

c = cnR.cursor()
cursor5 = c.execute("SELECT ID,ShapeVal from tblBeamSect")
for row in cursor5:
    bsectid.append(row[0])
    bsectinfo.append(row[1])
cnR.commit()

c = cnR.cursor()
cursor6 = c.execute("SELECT StdFlrID,SectID,JtID,ID,EccX,EccY,Rotation from tblColSeg")
for row in cursor6:
    cstdflr.append(row[0])
    csect.append(row[1])
    cjt.append(row[2])
    cid.append(row[3])
    ceccx.append(row[4])
    ceccy.append(row[5])
    crot.append(row[6])
cnR.commit()

c = cnR.cursor()
cursor7 = c.execute("SELECT ID,ShapeVal from tblColSect")
for row in cursor7:
    csectid.append(row[0])
    csectinfo.append(row[1])
cnR.commit()

conn = cnR.cursor()

# cnR.text_factory = bytes
cnR.text_factory = lambda x: str(x, 'gbk', 'ignore')

cursor8 = c.execute("SELECT ID,ShapeVal,Name from tblProperty")
for row in cursor8:
    spbid.append(row[0])
    spbshape.append(row[1])
    spbname.append(row[2])
cnR.commit()

brjt1=[]
brjt2=[]
brsect=[]
brstdflr=[]
brid=[]
brhd1=[]
brhd2=[]
c = cnR.cursor()
cursor9 = c.execute("SELECT Jt1ID,Jt2ID,SectID,StdFlrID,ID,HDiff1,HDiff2 from tblBraceSeg")
for row in cursor9:
    brjt1.append(row[0])
    brjt2.append(row[1])
    brsect.append(row[2])
    brstdflr.append(row[3])
    brid.append(row[4])
    brhd1.append(row[5])
    brhd2.append(row[6])
cnR.commit()

brsectid=[]
brsectinfo=[]
c = cnR.cursor()
cursor10 = c.execute("SELECT ID,ShapeVal from tblBraceSect")
for row in cursor10:
    brsectid.append(row[0])
    brsectinfo.append(row[1])
cnR.commit()

b = []
bstartz2 = []
for p in range(len(stdflrid)):
    for i in range(len(grid)):
        if bstdflr[i] == stdflrid[p]:
            for j in range(len(gridid)):
                if grid[i] == gridid[j]:
                    for k in range(len(jtid)):
                        if bjt1[j] == jtid[k]:
                            bstartx.append(bjtx[k])
                            bstarty.append(bjty[k])
                            bstartz2.append(jhdiff[k])
                            b.append(bid[i])

bendz2 = []
for p in range(len(stdflrid)):
    for i in range(len(grid)):
        if bstdflr[i] == stdflrid[p]:
            for j in range(len(gridid)):
                if grid[i] == gridid[j]:
                    for k in range(len(jtid)):
                        if bjt2[j] == jtid[k]:
                            bendx.append(bjtx[k])
                            bendy.append(bjty[k])
                            bendz2.append(jhdiff[k])

for p in range(len(stdflrid)):
    for i in range(len(grid)):
        if bstdflr[i] == stdflrid[p]:
            bstartz.append(levelb[p] + height[p])
            bendz.append(levelb[p] + height[p])

# for i in range(len(bstartz)):
#     bstartz[i] = bstartz[i] + bstartz2[i]
#     bendz[i] = bendz[i] + bendz2[i]

for p in range(len(stdflrid)):
    for i in range(len(grid)):
        if bstdflr[i] == stdflrid[p]:
            for j in range(len(bsectinfo)):
                if bsect[i] == bsectid[j] and bstdflr[i] in stdflrid:
                    bsection.append("'" + bsectinfo[j] + "'")

#斜撑
for p in range(len(stdflrid)):
    for i in range(len(brstdflr)):
        if brstdflr[i] == stdflrid[p]:
            for k in range(len(jtid)):
                if brjt1[i] == jtid[k]:
                    bstartx.append(bjtx[k])
                    bstarty.append(bjty[k])
                    bstartz2.append(jhdiff[k])
                    b.append(brid[i])


for p in range(len(stdflrid)):
    for i in range(len(brstdflr)):
        if brstdflr[i] == stdflrid[p]:
            for k in range(len(jtid)):
                if brjt2[i] == jtid[k]:
                    bendx.append(bjtx[k])
                    bendy.append(bjty[k])
                    bendz2.append(jhdiff[k])


for p in range(len(stdflrid)):
    for i in range(len(brstdflr)):
        if brstdflr[i] == stdflrid[p]:
            bstartz.append(levelb[p] + height[p])
            bendz.append(levelb[p] + height[p])


for i in range(len(bstartz)):
    bstartz[i] = bstartz[i] + bstartz2[i]
    bendz[i] = bendz[i] + bendz2[i]


for p in range(len(stdflrid)):
    for i in range(len(brstdflr)):
        if brstdflr[i] == stdflrid[p]:
            for j in range(len(brsectinfo)):
                if brsect[i] == brsectid[j] and brstdflr[i] in stdflrid:
                    bsection.append("'" + brsectinfo[j] + "'")


#柱子信息
c = []
for p in range(len(stdflrid)):
    for i in range(len(cstdflr)):
        if cstdflr[i] == stdflrid[p]:
            for j in range(len(jtid)):
                if cjt[i] == jtid[j] and cstdflr[i] in stdflrid:
                    cstartx.append(bjtx[j])
                    cstarty.append(bjty[j])
                    c.append(cid[i])
ceccx1=[]
ceccy1=[]
crot1=[]
for i in range(len(c)):
    for j in range(len(cid)):
        if c[i]==cid[j]:
            ceccx1.append(ceccx[j])
            ceccy1.append(ceccy[j])
            crot1.append((crot[j]))


for j in range(len(stdflrid)):
    for i in range(len(cstdflr)):
        if cstdflr[i] == stdflrid[j] and cstdflr[i] in stdflrid:
            cstartz.append(levelb[j])
            cendz.append(levelb[j] + height[j])

for p in range(len(stdflrid)):
    for i in range(len(cstdflr)):
        if cstdflr[i] == stdflrid[p]:
            for j in range(len(csectid)):
                if csect[i] == csectid[j] and cstdflr[i] in stdflrid:
                    csection.append("'" + csectinfo[j] + "'")

# 判断梁的点铰接
spbinfo = []
spbidd = []
for i in range(len(spbid)):
    if spbname[i] == "SpBeam":
        # for j in range(len(bid)):
        #     if spbid[i]== bid[j]:
        temp1 = spbshape[i]
        spbinfo.append(temp1[0:(temp1.find(",", (temp1.find(",")) + 1))])
        spbidd.append(spbid[i])

bsconn = []
beconn = []
for i in range(len(bstartx)):
    bsconn.append(0)
    beconn.append(0)
    for j in range(len(spbidd)):
        if b[i] == spbidd[j]:
            temp1 = spbinfo[j]
            bsconn[i]= temp1[0:(temp1.find(","))]
            beconn[i]= temp1[temp1.find(",") + 1:9]



# 判断柱的点铰接
spcinfo = []
spcidd = []
for i in range(len(spbid)):
    if spbname[i] == "SpColm":
        # for j in range(len(bid)):
        #     if spbid[i]== bid[j]:
        temp1 = spbshape[i]
        spcinfo.append(temp1[0:(temp1.find(",", (temp1.find(",")) + 1))])
        spcidd.append(spbid[i])

csconn = []
ceconn = []
for i in range(len(cstartx)):
    for j in range(len(spcidd)):
        if c[i] == spcidd[j]:
            temp1 = spcinfo[j]
            csconn.append(temp1[0:(temp1.find(","))])
            ceconn.append(temp1[temp1.find(",") + 1:9])



for i in range(len(bsconn)):
    if bsconn[i] == "3.00":
        bsconn[i] = "0"
    if beconn[i] == "3.00":
        beconn[i] = "0"

for i in range(len(csconn)):
    if csconn[i] == "3.00":
        csconn[i] = "0"
    if ceconn[i] == "3.00":
        ceconn[i] = "0"

# 梁z向偏移
for i in range(len(bstartx)):
    for j in range(len(bid)):
        if b[i] == bid[j]:
            bstartz[i] = float(bstartz[i]) + float(hd1[j])
            bendz[i] = float(bendz[i]) + float(hd2[j])

if os.access(r'Y:\数字化课题\数据库\ydb转换数据库.db',os.F_OK):
    db_file_path=r'Y:\数字化课题\数据库\ydb转换数据库.db'
else:
    db_file_path = r'C:\ProgramData\Autodesk\Revit\Addins\2018\数据库\ydb转换数据库.db'

cnY = sqlite3.connect(db_file_path)
# print("Opened database successfully")
cuY = cnY.cursor()



tbl1 = []
for tt in range(12):
    tbl1.append([])
for i in range(0, len(bstartx)):
    tbl1[0].append(bstartx[i])
    tbl1[1].append(bstarty[i])
    tbl1[2].append(bstartz[i])
    tbl1[3].append(bendx[i])
    tbl1[4].append(bendy[i])
    tbl1[5].append(bendz[i])
    tbl1[6].append(bsection[i])
    tbl1[7].append(0)
    tbl1[8].append(i + 1)
    tbl1[9].append("null")
    tbl1[10].append(bsconn[i])
    tbl1[11].append(beconn[i])
tbl1_T = list(zip(*tbl1))

# aaa = []
# tbl11 = []
# temp2 = []
# for row in tbl1_T:
#     aaa.append(row[6])
# for i in range(len(aaa)):
#     temp1 = aaa[i]
#     temp2.append(temp1[1:(temp1.find(","))])
#
# for i in range(len(tbl1_T)):
#     if int(temp2[i]) != 1:
#         tbl11.append(tbl1_T[i])



cuY.execute("drop table if exists tbl1;")
cuY.execute('''
CREATE TABLE tbl1 
    (
    BStartX            REAL,
    BStartY           REAL,
    BStartZ           REAL,
    BEndX           REAL,
    BEndY           REAL,
    BEndZ           REAL,
    BSection           TEXT,
    Tag                 INTEGER   DEFAULT 0,
    ID                INTEGER,
    RvtID             TEXT,
    BSConn            REAL,
    BEConn            REAL);''')
cnY.commit()
sql_insert = "INSERT INTO tbl1(BStartX,BStartY,BStartZ,BEndX,BEndY,BEndZ,BSection,Tag,ID,RvtID,BSConn,BEConn) VALUES"
sql_values = ""
sql_values1 = ""
for i in range(len(tbl1_T)):
    a = []
    List = tbl1_T[i]
    for j in range(len(List)):
        s = str(List[j])
        a.append(s)
    for k in range(0, len(a)):
        sql_values += a[k]
        sql_values += ","
    sql_values1 = "(" + sql_values.strip(',') + ")"
    sql_todo = sql_insert + sql_values1
    cuY = cnY.cursor()
    cuY.execute(sql_todo)
    sql_values = ""
    sql_value1 = ""
cnY.commit()

tbl2 = []
for tt in range(12):
    tbl2.append([])
for i in range(0, len(cstartx)):
    tbl2[0].append(cstartx[i])
    tbl2[1].append(cstarty[i])
    tbl2[2].append(cstartz[i])
    tbl2[3].append(cstartx[i])
    tbl2[4].append(cstarty[i])
    tbl2[5].append(cendz[i])
    tbl2[6].append(csection[i])
    tbl2[7].append(0)
    tbl2[8].append(i + 1)
    tbl2[9].append("null")
    tbl2[10].append(csconn[i])
    tbl2[11].append(ceconn[i])
tbl2_T = list(zip(*tbl2))

bbb = []
tbl22 = []
temp3 = []
for row in tbl2_T:
    bbb.append(row[6])
for i in range(len(bbb)):
    temp1 = bbb[i]
    temp3.append(temp1[1:(temp1.find(","))])

for i in range(len(tbl2_T)):
    if int(temp3[i]) != 1:
        tbl22.append(tbl2_T[i])

tbl2 = []
for tt in range(13):
    tbl2.append([])
for i in range(0, len(cstartx)):
    tbl2[0].append(cstartx[i])
    tbl2[1].append(cstarty[i])
    tbl2[2].append(cstartz[i])
    tbl2[3].append(cstartx[i])
    tbl2[4].append(cstarty[i])
    tbl2[5].append(cendz[i])
    tbl2[6].append(csection[i])
    tbl2[7].append(0)
    tbl2[8].append(i + 1)
    tbl2[9].append("null")
    tbl2[10].append(ceccx1[i])
    tbl2[11].append(ceccy1[i])
    tbl2[12].append(crot1[i])
    # tbl2[10].append(csconn[i])
    # tbl2[11].append(ceconn[i])
tbl2_T = list(zip(*tbl2))

# bbb = []
# tbl22 = []
# temp3 = []
# for row in tbl2_T:
#     bbb.append(row[6])
# for i in range(len(bbb)):
#     temp1 = bbb[i]
#     temp3.append(temp1[1:(temp1.find(","))])
#
# for i in range(len(tbl2_T)):
#     if int(temp3[i]) != 1:
#         tbl22.append(tbl2_T[i])

cuY.execute("drop table if exists tbl2;")
cuY.execute('''
CREATE TABLE tbl2
    (CStartX            REAL,
    CStartY           REAL,
    CStartZ           REAL,
    CEndX           REAL,
    CEndY           REAL,
    CEndZ           REAL,
    CSection           TEXT,
    Tag                 INTEGER    DEFAULT 0,
    ID               INTEGER,
    RvtID            TEXT,
    EccX             REAL,
    EccY             REAL,
    Rotation         REAL);''')
cnY.commit()
sql_insert = "INSERT INTO tbl2(CStartX,CStartY,CStartZ,CEndX,CEndY,CEndZ,CSection,Tag,ID,RvtID,EccX,EccY,Rotation) VALUES"
sql_values = ""
sql_values1 = ""
for i in range(len(tbl2_T)):
    a = []
    List = tbl2_T[i]
    for j in range(len(List)):
        s = str(List[j])
        a.append(s)
    for k in range(0, len(a)):
        sql_values += a[k]
        sql_values += ","
    sql_values1 = "(" + sql_values.strip(',') + ")"
    sql_todo = sql_insert + sql_values1
    cuY = cnY.cursor()
    cuY.execute(sql_todo)
    sql_values = ""
    sql_value1 = ""
cnY.commit()

tbl3 = []
for tt in range(2):
    tbl3.append([])
for i in range(0, len(levelb)):
    tbl3[0].append("'" + str(i + 1) + 'F' + "'")
    tbl3[1].append(levelb[i])
tbl3[0].append("'" + "RF" + "'")
tbl3[1].append(levelb[len(levelb) - 1] + height[len(levelb) - 1])
tbl3_T = list(zip(*tbl3))

cuY.execute("drop table if exists tbl3;")
cuY.execute('''
CREATE TABLE tbl3
    (Floor            TEXT,
    LevelB            REAL);''')
cnY.commit()
sql_insert = "INSERT INTO tbl3(Floor,LevelB) VALUES"
sql_values = ""
sql_values1 = ""
for i in range(len(tbl3_T)):
    a = []
    List = tbl3_T[i]
    for j in range(len(List)):
        s = str(List[j])
        a.append(s)
    for k in range(0, len(a)):
        sql_values += a[k]
        sql_values += ","
    sql_values1 = "(" + sql_values.strip(',') + ")"
    sql_todo = sql_insert + sql_values1
    cuY = cnY.cursor()
    cuY.execute(sql_todo)
    sql_values = ""
    sql_value1 = ""
cnY.commit()

cuY.execute("drop table if exists CombineBeam;")
cuY.execute('''
CREATE TABLE CombineBeam
    (id            TEXT,
    StartX         TEXT,   
    StartY        TEXT, 
    StartZ         TEXT,  
    EndX         TEXT,   
    EndY        TEXT, 
    EndZ         TEXT,
    ShapeValue    TEXT,
    Info          TEXT);''')
cnY.commit()
# sql_insert = "INSERT INTO CombineBeam(id,StartX,StartY,StartZ,EndX,EndY,EndZ,ShapeValue) VALUES(1,1,1,1,1,1,1,1)"
# cuY.execute(sql_insert)
# cnY.commit()
