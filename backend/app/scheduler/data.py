"""
Authoritative reference data for the timetable scheduler.

All inline markdown strings from the original timetable_app.py are collected here.
This is the single source of truth for baseline timetable, faculty info, and room data.
"""
from __future__ import annotations

EXISTING_TIMETABLE_MD = """
| Day | Program | Semester | Time | Subject / Faculty / Room |
|---|---|---|---|---|
| Monday | B.Tech. | 3rd | 10:00-12:00 | PBn(EVS):R#303 |
| Monday | B.Tech. | 3rd | 12:00-14:00 | SK(DL):R#303 |
| Monday | B.Tech. | 3rd | 14:30-17:30 | RD(DL-P):R#207A |
| Monday | B.Tech. | 5th | 10:00-14:00 | AD(AIML):R#207B |
| Monday | B.Tech. | 5th | 14:30-17:30 | AD(AIML-P):R#207B |
| Monday | B.Tech. | 7th | 10:00-12:00 | SN(AGT):R#208 |
| Monday | B.Tech. | 7th | 12:00-14:00 | RD(IDM):R#208 |
| Monday | B.Tech. | 7th | 14:30-16:30 | SKS(CD):R#403 |
| Monday | M.Sc. | 1st | 10:00-12:00 | SKS(CD):R#403 |
| Monday | M.Sc. | 1st | 12:00-14:00 | AB(AIML):R#403 |
| Monday | M.Sc. | 1st | 14:30-17:30 | AB(AIML-P):R#208 |
| Monday | M.Sc. | 3rd | 10:00-13:00 | AG+(AI-P):R#205 |
| Monday | M.Sc. | 3rd | 13:00-14:00 | DK(IPPR):R#205 |
| Monday | M.Sc. | 3rd | 14:30-17:30 | DK(IPPR):R#205 |
| Monday | M.Tech. | 1st | 11:00-14:00 | PB(AA-P):R#209 |
| Monday | M.Tech. | 1st | 14:30-16:30 | SK(WMC):R#209 |
| Tuesday | B.Tech. | 3rd | 12:00-14:00 | RD(MM):R#205 |
| Tuesday | B.Tech. | 3rd | 14:30-17:30 | SCh(DM):R#303 |
| Tuesday | B.Tech. | 5th | 10:00-12:00 | SC(DBMS):R#207B |
| Tuesday | B.Tech. | 5th | 12:00-14:00 | SK(CN):R#207B |
| Tuesday | B.Tech. | 5th | 14:30-17:30 | SC(DBMS-P):R#207B |
| Tuesday | B.Tech. | 7th | 12:00-14:00 | SKS(CD):R#303 |
| Tuesday | B.Tech. | 7th | 14:30-17:30 | SKS(CD-P):R#205 |
| Tuesday | M.Sc. | 1st | 10:00-12:00 | TD(ES&IoT):R#208 |
| Tuesday | M.Sc. | 1st | 12:00-14:00 | NC(ADBMS):R#208 |
| Tuesday | M.Sc. | 1st | 14:30-17:30 | NC(ADBMS-P):R#208 |
| Tuesday | M.Tech. | 1st | 11:00-14:00 | PB(AA):R#209 |
| Tuesday | M.Tech. | 1st | 14:30-16:30 | SGC(MFCS):R#209 |
| Tuesday | M.Tech. | 1st | 16:30-17:30 | SR(PE-1:SC):R#209 |
| Tuesday | M.Tech. | 3rd | 12:00-14:00 | RKP(VLSI):R#403 |
| Wednesday | B.Tech. | 3rd | 10:00-12:00 | PB(DS):R#205 |
| Wednesday | B.Tech. | 3rd | 12:00-14:00 | RD(M):R#205 |
| Wednesday | B.Tech. | 3rd | 14:30-17:30 | RKP(PST):R#205 |
| Wednesday | B.Tech. | 5th | 12:00-14:00 | NC(SE):R#207B |
| Wednesday | B.Tech. | 5th | 14:30-17:30 | SK(CN-P):R#207B |
| Wednesday | B.Tech. | 7th | 12:00-14:00 | SN(AGT):R#303 |
| Wednesday | B.Tech. | 7th | 14:30-17:30 | SN(AGT-P):R#207A |
| Wednesday | M.Sc. | 1st | 10:00-12:00 | GM(CG):R#208 |
| Wednesday | M.Sc. | 1st | 12:00-14:00 | GM(CG-P):R#208 |
| Wednesday | M.Sc. | 1st | 14:30-15:30 | GM(CG-P):R#208 |
| Wednesday | M.Sc. | 1st | 15:30-17:30 | AB(AIML):R#403 |
| Wednesday | M.Tech. | 1st | 10:00-12:00 | SC(WMC):R#209 |
| Wednesday | M.Tech. | 1st | 12:00-14:00 | DC(RM):R#209 |
| Wednesday | M.Tech. | 1st | 14:30-17:30 | SR+(PE-1:SC-P):R#208 |
| Wednesday | M.Tech. | 3rd | 10:00-12:00 | RS(Bioinformatics):R#403 |
| Wednesday | M.Tech. | 3rd | 12:00-14:00 | SKS(DeepLearning):R#403 |
| Thursday | B.Tech. | 3rd | 10:00-12:00 | PBn(EVS):R#303 |
| Thursday | B.Tech. | 3rd | 12:00-14:00 | SK(DL):R#303 |
| Thursday | B.Tech. | 3rd | 14:30-17:30 | SCh(DM):R#303 |
| Thursday | B.Tech. | 5th | 12:00-14:00 | SC(DBMS):R#207B |
| Thursday | B.Tech. | 5th | 14:30-17:30 | NC(SE-P):R#207B |
| Thursday | B.Tech. | 7th | 12:00-14:00 | RD(IDM):R#205 |
| Thursday | B.Tech. | 7th | 14:30-17:30 | RD(IDM-P):R#205 |
| Thursday | M.Sc. | 1st | 10:00-12:00 | SKS(CD):R#208 |
| Thursday | M.Sc. | 1st | 12:00-14:00 | NC(ADBMS):R#208 |
| Thursday | M.Sc. | 1st | 14:30-17:30 | SKS(CD-P):R#208 |
| Thursday | M.Tech. | 1st | 10:00-12:00 | PB(AA):R#209 |
| Thursday | M.Tech. | 1st | 12:00-14:00 | SD(ERPW):R#209 |
| Thursday | M.Tech. | 1st | 14:30-17:30 | SR(PE-1:SC):R#209 |
| Thursday | M.Tech. | 3rd | 12:00-14:00 | RKP(VLSI):R#403 |
| Friday | B.Tech. | 3rd | 10:00-12:00 | SK(DL-P):R#207B |
| Friday | B.Tech. | 3rd | 12:00-14:00 | PB(DS):R#207B |
| Friday | B.Tech. | 3rd | 14:30-17:30 | PB(DS-P):R#207B |
| Friday | B.Tech. | 5th | 10:00-12:00 | NC(SE):R#205 |
| Friday | B.Tech. | 5th | 12:00-14:00 | SK(CN):R#205 |
| Friday | B.Tech. | 5th | 14:30-17:30 | SC(DBMS-P):R#205 |
| Friday | M.Sc. | 1st | 10:00-12:00 | GM(CG):R#208 |
| Friday | M.Sc. | 1st | 12:00-14:00 | TD(ES&IoT):R#208 |
| Friday | M.Sc. | 1st | 14:30-17:30 | TG(ES&IoT-P):R#208 |
| Friday | M.Tech. | 1st | 12:00-14:00 | SGC(MFCS):R#209 |
| Friday | M.Tech. | 1st | 14:30-17:30 | DC(RM):R#209 |
| Saturday | M.Sc. | 1st | 10:00-12:00 | TD(ES&IoT):R#207B |
| Saturday | M.Sc. | 3rd | 12:00-14:00 | AG(AI):R#207B |
| Saturday | M.Sc. | 3rd | 14:30-16:30 | AG(AI):R#207B |
| Saturday | M.Tech. | 3rd | 10:00-12:00 | SKS(DeepLearning):R#208 |
| Saturday | M.Tech. | 3rd | 12:00-14:00 | RS(Bioinformatics):R#208 |
"""

FACULTY_MASTER_MD = """
CRITICAL — EASILY CONFUSED SHORT NAMES (read carefully before assigning any class):
  • SK  = Surinmal Khatua  — teaches Digital Logic, Computer Networks, WMC. NOT the same as SKS.
  • SKS = Sanjit K Setua   — teaches Compiler Design, Deep Learning. NOT the same as SK.
  • PB  = Pritha Banerjee  — teaches Data Structure. NOT the same as PBn.
  • PBn = Priya Banerjee   — teaches Environmental Science only. NOT the same as PB.
Never swap SK with SKS or PB with PBn. They are entirely different people with different subjects.

| Professor | Short Name | Allocated Subjects / Duties |
|---|---|---|
| Sumit Chakraborty | SCh | Discrete Mathematics |
| Pritha Banerjee | PB [≠ PBn] | Data Structure; Data Structure Lab; Advanced Algorithms; AA Lab |
| Surinmal Khatua | SK [≠ SKS] | Digital Logic; Computer Networks; CN Lab; Wireless & Mobile Computing; Digital Logic & Microprocessor Lab |
| Rajib Das | RD | Microprocessor & Microcontroller; Introduction to Data Mining; IDM Lab; Digital Logic & Microprocessor Lab |
| Priya Banerjee | PBn [≠ PB] | Environmental Science |
| Sankhayan Choudhury | SC | DBMS; DBMS Lab; Wireless & Mobile Computing |
| Nabendu Chaki | NC | Software Engineering; SE Lab; Advance DBMS; ADBMS Lab |
| Amartya Dutta | AD | AI & Machine Learning; AIML Lab |
| Sanjit K Setua | SKS [≠ SK] | Compiler Design; CD Lab; Deep Learning |
| Somen Nandy | SN | Algorithmic Graph Theory; AGT Lab |
| Gautam Mahapatra | GM | Computer Graphics; CG Lab |
| Ansuman Banerjee | AB | Artificial Intelligence & Machine Learning; AIML Lab |
| Tathagata Das | TD | Environment Systems & Internet of Things; associated lab |
| Dhiman Karmakar | DK | Image Processing & Pattern Recognition |
| Anupam Ghosh | AG | Artificial Intelligence; AI Lab |
| Shruti Gan Choudhuri | SGC | Mathematical Foundations of Computer Science |
| Samir Roy | SR | Soft Computing; Soft Computing Lab |
| Susmita Das | SD | English for Research Paper Writing |
| Debesh Choudhury | DC | Research Methodology |
| Rituparna Sinha | RS | Bioinformatics |
| Rajat K Pal | RKP | VLSI Design; Problem Solving Techniques |
"""

LAB_ALLOCATION_MD = """
| Short Name(s) | Lab / Additional Allocation |
|---|---|
| PB  (Pritha Banerjee — Data Structure teacher) | Data Structure Lab |
| SK  (Surinmal Khatua — Digital Logic / Networks teacher) | Digital Logic & Microprocessor Lab; CN Lab |
| PBn (Priya Banerjee  — EVS teacher ONLY) | Environmental Science |
| SC | DBMS Lab |
| NC | SE Lab; ADBMS Lab |
| AD | AIML Lab |
| SKS (Sanjit K Setua — Compiler Design / Deep Learning teacher) | CD Lab |
| SN | AGT Lab |
| RD | IDM Lab |
| GM | CG Lab |
| AB | AIML Lab |
| TD | ES & IoT Lab |
| AG | AI Lab |
| SR | Soft Computing Lab |
| Others | Use authoritative subject/teacher file |
"""

ROOM_EXAMPLE_MD = """
EXAMPLE ONLY, not confirmed authoritative unless the user supplies real room data below.
| Room | Blackboard | Projector | Lab |
|---|---|---|---|
| R#205 | Yes | Yes | No |
| R#207A | Yes | No | Yes |
| R#207B | Yes | Yes | Yes |
| R#208 | Yes | Yes | No |
| R#209 | Yes | Yes | No |
| R#303 | Yes | Yes | No |
| R#403 | Yes | Yes | No |
"""
