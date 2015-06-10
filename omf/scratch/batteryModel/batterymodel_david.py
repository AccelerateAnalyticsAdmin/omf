# -*- coding: utf-8 -*-
"""
Created on Wed May 27 16:59:15 2015
@author: george
"""

import csv
import datetime
import time
from dateutil.parser import parse

def findPeakShave(
	csvFileName = "OlinBeckenhamScada.csv",
	cellCapacity = 100, # kWhr
	cellDischarge = 30, # kW
	cellCharge = 30, # kW
	cellQty = 50,
	battEff = .92): # 0<battEff<1
	# Pack variables.
	battCapacity = cellQty * cellCapacity
	battDischarge = cellQty * cellDischarge
	battCharge = cellQty * cellCharge
	#  Load our CSV file into a list
	dc = []
	for row in csv.DictReader(open(csvFileName)):
		d = parse(row['timestamp'])
		dc.append({'timestamp': int(time.mktime(d.timetuple())), 'month': int(d.month-1), 'day': d.weekday(), 'power': int(row['power'])})
	ps = [cellDischarge * cellQty for x in range(12)]
	# Find our demand peaks per month.
	monthlyPeakDemand  = [0 for x in range(12)]
	for row in dc:
			monthlyPeakDemand[row['month']] = max(row['power'], monthlyPeakDemand[row['month']])
	capacityLimited = True
	while capacityLimited == True:
		battSoC = battCapacity # Battery state of charge; begins full.
		battDoD = [battCapacity for x in range(12)] # Depth-of-discharge every month.
		for row in dc:
			powerUnderPeak  = monthlyPeakDemand[row['month']] - row['power'] - ps[row['month']]
			isCharging      = powerUnderPeak > 0
			isDischarging   = powerUnderPeak <= 0
			charge    = isCharging    * min(powerUnderPeak * battEff, # Charge rate <= new monthly peak - row['power']
											battCharge, # Charge rate <= battery maximum charging rate.
											battCapacity - battSoC) # Charge rage <= capacity remaining in battery.
			discharge = isDischarging * min(abs(powerUnderPeak), # Discharge rate <= new monthly peak - row['power']
											abs(battDischarge), # Discharge rate <= battery maximum charging rate.
											abs(battSoC+.001)) # Discharge rate <= capacity remaining in battery.
			# (Dis)charge battery
			battSoC += charge
			battSoC -= discharge
			row['netpower'] = row['power'] + charge/battEff - discharge
			# Update minimum state-of-charge for this month.
			battDoD[row['month']] = min(battSoC,battDoD[row['month']])
			row['battSoC'] = battSoC
		ps = [ps[month]-(battDoD[month] < 0) for month in range(12)]
		capacityLimited = min(battDoD) < 0
	oldDemandCurve = [x['power'] for x in dc]
	newDemandCurve = [x['netpower'] for x in dc]
	socCurve = [x['battSoC'] for x in dc]
	return oldDemandCurve, newDemandCurve, socCurve

import matplotlib.pyplot as plt
(x1, x2, x3) = findPeakShave()
plt.plot(x1)
plt.plot(x2)
plt.plot(x3)
plt.show()


# TODO:
# XXX Refactor existing solution.
# XXX Add SoC graph.
# OOO vertical lines.
# OOO Financial variables.