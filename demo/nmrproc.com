#!/bin/csh

nmrPipe -in test.fid \
| nmrPipe  -fn SP -off 0.5 -end 0.98 -pow 2 -c 0.5    \
| nmrPipe  -fn ZF -auto                               \
| nmrPipe  -fn FT                                     \
| nmrPipe  -fn PS -p0 40.0  -p1 -60.0 -di             \
| nmrPipe  -fn EXT -left -sw                          \
| nmrPipe  -fn TP                                     \
| nmrPipe  -fn SP -off 0.5 -end 0.98 -pow 2 -c 0.5    \
| nmrPipe  -fn ZF -auto                               \
| nmrPipe  -fn FT                                     \
| nmrPipe  -fn PS -p0 25.0 -p1 0.0 -di               \
   -verb -ov -out test.ft2
