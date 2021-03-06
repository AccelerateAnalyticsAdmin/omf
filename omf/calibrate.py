import csv, datetime as dt, json, tempfile
from matplotlib import pyplot as plt
import os
from os.path import join as pJoin
# OMF imports
import feeder
from solvers import gridlabd
import random
# Plotting
import numpy as np
import matplotlib
from matplotlib import pyplot as plt

def omfCalibrate(workDir, feederPath, scadaPath, simStartDate, simLength, calibrateError=0.05):
	'''calibrates a feeder and saves the calibrated tree at a location'''
	with open(feederPath, "r") as jsonIn:
		feederJson = json.load(jsonIn)
		tree = feederJson.get("tree", {})
	# Process scada data.
	gridlabdDir = pJoin(workDir,"gridlabD")
	scadaSubPower = _processScadaData(gridlabdDir,scadaPath, simStartDate)
	# Force FBS powerflow, because NR fails a lot.
	for key in tree:
		if tree[key].get("module","").lower() == "powerflow":
			tree[key] = {"module":"powerflow","solver_method":"FBS"}
	# Attach player.
	classOb = {'omftype':'class player','argument':'{double value;}'}
	playerOb = {"object":"player", "property":"value", "name":"scadaLoads", "file":"subScada.player", "loop":"0"}
	maxKey = feeder.getMaxKey(tree)
	playerKey = maxKey + 2
	tree[maxKey+1] = classOb
	tree[playerKey] = playerOb
	# Make loads reference player.
	loadTemplate = {"object": "triplex_load",
		"power_pf_12": "0.95",
		"impedance_pf_12": "0.98",
		"power_pf_12": "0.90",
		"impedance_fraction_12": "0.7",
		"power_fraction_12": "0.3"}
	loadTemplateR = {"object": "load",
		"impedance_pf_A": "0.98",
		"impedance_pf_B": "0.98",
		"impedance_pf_C": "0.98",
		"power_pf_A": "0.90",
		"power_pf_B": "0.90",
		"power_pf_C": "0.90",
		"impedance_fraction_A": "0.7",
		"impedance_fraction_B": "0.7",
		"impedance_fraction_C": "0.7",
		"power_fraction_A": "0.3",
		"power_fraction_B": "0.3",
		"power_fraction_C": "0.3"}		
	for key in tree:
		ob = tree[key]
		if ob.get("object","") == "triplex_node" and ob.get("power_12","") != "":
			# Add to triplex_nodes.
			newOb = dict(loadTemplate)
			newOb["name"] = ob.get("name", "")
			newOb["parent"] = ob.get("parent", "")
			newOb["phases"] = ob.get("phases", "")
			newOb["nominal_voltage"] = ob.get("nominal_voltage","")
			newOb["latitude"] = ob.get("latitude","0")
			newOb["longitude"] = ob.get("longitude","0")
			oldPow = ob.get("power_12","").replace("j","d")
			pythagPower = gridlabd._strClean(oldPow)
			newOb["base_power_12"] = "scadaLoads.value*" + str(pythagPower)
			tree[key] = newOb
		elif ob.get("object","") == "load":
			# Add to residential_loads too.
			newOb = dict(loadTemplateR)
			newOb["name"] = ob.get("name", "")
			newOb["parent"] = ob.get("parent", "")
			newOb["phases"] = ob.get("phases", "")
			newOb["load_class"] = ob.get("load_class", "")
			newOb["nominal_voltage"] = ob.get("nominal_voltage","")
			newOb["latitude"] = ob.get("latitude","0")
			newOb["longitude"] = ob.get("longitude","0")
			try:
				oldPow = ob.get("constant_power_A","").replace("j","d")
				pythagPower = gridlabd._strClean(oldPow)
				newOb["base_power_A"] = "scadaLoads.value*" + str(pythagPower)
			except:
				pass
			try:
				oldPow = ob.get("constant_power_B","").replace("j","d")
				pythagPower = gridlabd._strClean(oldPow)
				newOb["base_power_B"] = "scadaLoads.value*" + str(pythagPower)
			except:
				pass
			try:
				oldPow = ob.get("constant_power_C","").replace("j","d")
				pythagPower = gridlabd._strClean(oldPow)
				newOb["base_power_C"] = "scadaLoads.value*" + str(pythagPower)
			except:
				pass
			tree[key] = newOb
	# Convert swing bus to a meter.
	for key in tree:
		if tree[key].get('bustype','').lower() == 'swing' and tree[key].get('object','') != 'meter':
			swingName = tree[key].get('name')
			regIndex = key
			tree[key]['object'] = 'meter'	
	# Search for the substation meter and attach a recorder there.
	for key in tree:
		if tree[key].get('bustype','').lower() == 'swing':
			swingName = tree[key].get('name')
	recOb = {"object": "recorder",
		"parent": swingName,
		"property": "measured_real_power,measured_reactive_power,measured_power",
		"file": "caliSub.csv",
		"interval": "900"}
	outputRecorderKey = maxKey + 3
	tree[outputRecorderKey] = recOb
	feeder.adjustTime(tree, simLength, "hours", simStartDate['Date'].strftime("%Y-%m-%d %H:%M:%S"))
	# Run Gridlabd, calculate scaling constant.
	def runPowerflowIter(tree,scadaSubPower):
		'''Runs powerflow once, then iterates.'''
		# Run initial powerflow to get power.
		print "Running initial calibration powerflow."
		output = gridlabd.runInFilesystem(tree, keepFiles=True, workDir=gridlabdDir)
		outRealPow = output["caliSub.csv"]["measured_real_power"]
		outImagPower = output["caliSub.csv"]["measured_reactive_power"]
		outAppPowerKw = [(x[0]**2 + x[1]**2)**0.5/1000 for x in zip(outRealPow, outImagPower)]
		lastFile = "subScada.player"
		nextFile = "subScadaCalibrated.player"
		nextPower = outAppPowerKw
		error = (sum(outRealPow[1:simLength])/1000-sum(scadaSubPower[1:simLength]))/sum(scadaSubPower[1:simLength])
		iteration = 1
		while abs(error)>calibrateError and iteration<5:
			# Run calibration and iterate up to 5 times.
			SCAL_CONST = sum(scadaSubPower[1:simLength])/sum(nextPower[1:simLength])
			print "Calibrating loads, running powerflow again. Our SCAL_CONST is: ", SCAL_CONST
			newPlayData = []
			with open(pJoin(gridlabdDir, lastFile), "r") as playerFile:
				for line in playerFile:
					(key,val) = line.split(',')
					newPlayData.append(str(key) + ',' + str(float(val)*SCAL_CONST) + "\n")
			with open(pJoin(gridlabdDir, nextFile), "w") as playerFile:
				for row in newPlayData:
					playerFile.write(row)
			tree[playerKey]["file"] = nextFile
			tree[outputRecorderKey]["file"] = "caliSubCheck.csv"
			nextOutput = gridlabd.runInFilesystem(tree, keepFiles=True, workDir=gridlabdDir)
			outRealPowIter = nextOutput["caliSubCheck.csv"]["measured_real_power"]
			outImagPowerIter = nextOutput["caliSubCheck.csv"]["measured_reactive_power"]
			nextAppKw = [(x[0]**2 + x[1]**2)**0.5/1000
				for x in zip(outRealPowIter, outImagPowerIter)]
			lastFile = nextFile
			nextFile = "subScadaCalibrated"+str(iteration)+".player"
			nextPower = nextAppKw
			# Compute error and iterate.
			error = (sum(outRealPowIter[1:simLength])/1000-sum(scadaSubPower[1:simLength]))/sum(scadaSubPower[1:simLength])
			iteration+=1
			print "Error:", abs(error*100), "% Iteration:", iteration
		return outRealPow, outRealPowIter, lastFile, iteration
	outRealPow, outRealPowIter, lastFile, iteration = runPowerflowIter(tree,scadaSubPower)
	caliPowVectors = [[float(element) for element in scadaSubPower[1:simLength]], [float(element)/1000 for element in outRealPow[1:simLength]], [float(element)/1000 for element in outRealPowIter[1:simLength]]]
	labels = ["scadaSubPower","initialGuess","finalGuess"]
	colors = ['red','lightblue','blue']
	chartData = {"Title":"Substation Calibration Check (Iterated "+str(iteration+1)+"X)", "fileName":"caliCheckPlot", "colors":colors,"labels":labels, "timeZone":simStartDate['timeZone']}
	plotLine(workDir, caliPowVectors, chartData, simStartDate['Date']+dt.timedelta(hours=1), 'hours')
	# Write the final output.
	with open(pJoin(workDir,"calibratedFeeder.json"),"w") as outJson:
		playerString = open(pJoin(gridlabdDir,lastFile)).read()
		feederJson["attachments"][lastFile] = playerString
		feederJson["tree"] = tree
		json.dump(feederJson, outJson, indent=4)
	return

def _processScadaData(workDir,scadaPath, simStartDate):
	'''generate a SCADA player file from raw SCADA data'''
	with open(scadaPath,"r") as scadaFile:
		scadaReader = csv.DictReader(scadaFile, delimiter='\t')
		allData = [row for row in scadaReader]
	scadaSubPower = [float(row["power"]) for row in allData]
	firstDateTime = dt.datetime.strptime(allData[1]["timestamp"], "%m/%d/%Y %H:%M:%S")
	# Write the player.
	maxPower = max(scadaSubPower)
	positionFound = 0
	with open(pJoin(workDir,"subScada.player"),"w") as playFile:
		for i,row in enumerate(allData):
			timestamp = dt.datetime.strptime(row["timestamp"], "%m/%d/%Y %H:%M:%S")
			if timestamp >= simStartDate['Date']:
				if positionFound == 0: positionFound = i
				power = float(row["power"]) / maxPower
				line = timestamp.strftime("%Y-%m-%d %H:%M:%S") + " " + simStartDate['timeZone'] + "," + str(power) + "\n"
				playFile.write(line)
	return scadaSubPower[positionFound:]

def attachVolts(workDir, feederPath, voltVectorA, voltVectorB, voltVectorC, simStartDate, simLength):
	'''read voltage vectors of 3 different phases, run gridlabd, and attach output to the feeder.'''
	gridlabdDir = pJoin(workDir,"gridlabD")
	try:
		timeStamp = [simStartDate['Date']]
		for x in range (1, 8760):
			timeStamp.append(timeStamp[x-1] + dt.timedelta(hours=1))
		firstDateTime = timeStamp[1]
		with open(pJoin(gridlabdDir,"phaseAVoltage.player"),"w") as voltFile:
			for x in range(0, 8760):
				timestamp = timeStamp[x]
				voltage = str("%0.2f"%float(voltVectorA[x]))+"+0j"
				line = timestamp.strftime("%Y-%m-%d %H:%M:%S") + " " + simStartDate['timeZone'] + "," + str(voltage) + "\n"
				voltFile.write(line)
		with open(pJoin(gridlabdDir,"phaseBVoltage.player"),"w") as voltFile:
			for x in range(0, 8760):
				timestamp = timeStamp[x]
				voltage = str("%0.2f"%float(voltVectorB[x]))+"-"+str("%0.4f"%float(random.uniform(6449,6460)))+"j"
				line = timestamp.strftime("%Y-%m-%d %H:%M:%S") + " " + simStartDate['timeZone'] + "," + str(voltage) + "\n"
				voltFile.write(line)
		with open(pJoin(gridlabdDir,"phaseCVoltage.player"),"w") as voltFile:
			for x in range(0, 8760):
				timestamp = timeStamp[x]
				voltage = str("%0.2f"%float(voltVectorC[x]))+"+"+str("%0.4f"%float(random.uniform(6449,6460)))+"j"
				line = timestamp.strftime("%Y-%m-%d %H:%M:%S") + " " + simStartDate['timeZone'] + "," + str(voltage) + "\n"
				voltFile.write(line)
		with open(feederPath, "r") as jsonIn:
			feederJson = json.load(jsonIn)
			tree = feederJson.get("tree", {})
		# Find swingNode name.
		for key in tree:
			if tree[key].get('bustype','').lower() == 'swing':
				swingName = tree[key].get('name')
		# Attach player.
		classOb = {'omftype':'class player','argument':'{double value;}'}
		voltageObA = {"object":"player", "property":"voltage_A", "file":"phaseAVoltage.player", "loop":"0", "parent":swingName}
		voltageObB = {"object":"player", "property":"voltage_B", "file":"phaseBVoltage.player", "loop":"0", "parent":swingName}
		voltageObC = {"object":"player", "property":"voltage_C", "file":"phaseCVoltage.player", "loop":"0", "parent":swingName}
		maxKey = feeder.getMaxKey(tree)
		voltplayerKeyA = maxKey + 2
		voltplayerKeyB = maxKey + 3
		voltplayerKeyC = maxKey + 4
		tree[maxKey+1] = classOb
		tree[voltplayerKeyA] = voltageObA
		tree[voltplayerKeyB] = voltageObB
		tree[voltplayerKeyC] = voltageObC
		# Adjust time and run output.
		feeder.adjustTime(tree, simLength, "hours", firstDateTime.strftime("%Y-%m-%d %H:%M:%S"))
		output = gridlabd.runInFilesystem(tree, keepFiles=True, workDir=gridlabdDir)
		# Write the output.
		with open(pJoin(workDir,"calibratedFeeder.json"),"w") as outJson:
			playerStringA = open(pJoin(gridlabdDir,"phaseAVoltage.player")).read()
			playerStringB = open(pJoin(gridlabdDir,"phaseBVoltage.player")).read()
			playerStringC = open(pJoin(gridlabdDir,"phaseCVoltage.player")).read()
			feederJson["attachments"]["phaseAVoltage.player"] = playerStringA
			feederJson["attachments"]["phaseBVoltage.player"] = playerStringB
			feederJson["attachments"]["phaseCVoltage.player"] = playerStringC
			feederJson["tree"] = tree
			json.dump(feederJson, outJson, indent=4)
		return pJoin(workDir,"calibratedFeeder.json"), True
	except:
		print "Failed to run gridlabD with voltage players."
		return "", False

def plotLine(workDir, powerVec, chartData, startTime, simLengthUnits):
	''' Plots vector data with given plotname to filename.png.
		Chartdata accepts: title, timezone, y label, legend labels&colors.'''
	plt.style.use('fivethirtyeight')
	plt.figure("Power")
	plt.title(chartData['Title'], fontsize=12)
	plt.xlabel("Time "+"("+chartData.get('timeZone','')+")")
	plt.ylabel(chartData.get('yAxis','Real Power (kW)'))
	if str(type(chartData['labels'])) == "<type \'list\'>": timeLength = len(powerVec[0])
	else: timeLength = len(powerVec)
	if simLengthUnits == 'hours': x = np.array([(startTime+dt.timedelta(hours=i)) for i in range(timeLength)])
	elif simLengthUnits == 'minutes': x = np.array([(startTime+dt.timedelta(minutes=i)) for i in range(timeLength)])
	elif simLengthUnits == 'days': x = np.array([(startTime+dt.timedelta(days=i)) for i in range(timeLength)])
	if str(type(chartData['labels'])) == "<type \'list\'>":
		for i in range(len(powerVec)):
			colors = chartData.get("colors","['red','black','green']")
			pws = plt.plot(x, powerVec[i], colors[i], label=str(chartData["labels"][i]))
		plt.legend(loc=1,prop={'size':6})
	else:
		if chartData.get('labels','') != "":
			pws = plt.plot(x, powerVec, label=str(chartData["labels"]))
			plt.legend(loc=1,prop={'size':6})
		else: pws = plt.plot(x, powerVec)
	# Add boundaries.
	if chartData.get('boundaries','') != "":
		for i in range(len(chartData['boundaries'])):
			boundary = np.array([float(chartData['boundaries'][i]) for times in range(timeLength)])
			pws = plt.plot(x, boundary,'r--')
	# Set axis text sizes.
	plt.tick_params(axis='both', which='major', labelsize=8)
	plt.tick_params(axis='x', which='minor', labelsize=5)
	plt.gcf().autofmt_xdate()
	plt.tight_layout()
	plt.margins(x=0.1,y=0.2)
	# Save and close plot.
	plt.savefig(pJoin(workDir,chartData['fileName']+".png"))
	plt.close()

def _tests():
	print "Beginning to test calibrate.py"
	workDir = tempfile.mkdtemp()
	try: os.mkdir(pJoin(workDir,"gridlabD"))
	except: pass	
	print "Currently working in: ", workDir
	scadaPath = pJoin("uploads", "FrankScada.csv")
	feederPath = pJoin("data", "Feeder", "public","ABEC Frank pre calib.json")
	simDate = dt.datetime.strptime("4/13/2011 09:00:00", "%m/%d/%Y %H:%M:%S") # Spring peak.
	simStartDate = {"Date":simDate,"timeZone":"PST"}
	simLength = 24*7
	print "Simulation Date:", simStartDate['Date'], "for", str(simLength), "hours."
	voltVectorA = [random.uniform(7380,7620) for x in range(0,8760)]
	voltVectorC = [-random.uniform(3699,3780) for x in range(0, 8760)]
	voltVectorB = [-random.uniform(3699,3795) for x in range(0, 8760)]
	print "Running gridlabD with voltage players."
	voltFeederPath, outcome = attachVolts(workDir, feederPath, voltVectorA, voltVectorB, voltVectorC, simStartDate, simLength)
	try: 
		assert None == omfCalibrate(workDir, voltFeederPath, scadaPath, simStartDate, simLength), "feeder calibration failed"
		print "\n  Success! Ran calibrate with voltage players!"
	except: 
		print "Failed to run calibrate with voltage players. Running only calibrate now."
		assert None == omfCalibrate(workDir, feederPath, scadaPath, simStartDate, simLength), "feeder calibration failed"
		print "\n  Success! Ran calibrate!"

if __name__ == '__main__':
	_tests()