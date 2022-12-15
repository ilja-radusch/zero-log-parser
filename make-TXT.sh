#!/bin/sh


for I in `ls -1 *.bin`
do
	python zero_log_parser.py $I 
done

