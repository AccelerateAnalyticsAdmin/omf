''' Web server for model-oriented OMF interface. '''

from flask import Flask, send_from_directory, request, redirect, render_template, session, abort, jsonify, Response, url_for
from jinja2 import Template
from multiprocessing import Process
from passlib.hash import pbkdf2_sha512
import json, os, flask_login, hashlib, random, time, datetime as dt, shutil, boto.ses
import models, feeder, milToGridlab
import cymeToGridlab

app = Flask("web")
URL = "http://www.omf.coop"
_omfDir = os.path.dirname(os.path.abspath(__file__))

###################################################
# HELPER FUNCTIONS
###################################################

def safeListdir(path):
	''' Helper function that returns [] for dirs that don't exist. Otherwise new users can cause exceptions. '''
	try: return [x for x in os.listdir(path) if not x.startswith(".")]
	except:	return []

def getDataNames():
	''' Query the OMF datastore to get names of all objects.'''
	try:
		currUser = User.cu()
	except:
		currUser = "public"
	climates = [x[:-5] for x in safeListdir("./data/Climate/")]
	feeders = []
	for (dirpath, dirnames, filenames) in os.walk(os.path.join(_omfDir, "data","Model", currUser)):
		for file in filenames:
			if '.omd' in file and file != 'feeder.omd':
				feeders.append({'name': file.strip('.omd'), 'model': dirpath.split('/')[-1]})	
	return {"climates":sorted(climates), "feeders":feeders, "currentUser":currUser}

@app.before_request
def csrf_protect():
	pass
	## NOTE: when we fix csrf validation this needs to be uncommented.
	# if request.method == "POST":
	#	token = session.get("_csrf_token", None)
	#	if not token or token != request.form.get("_csrf_token"):
	#		abort(403)

###################################################
# AUTHENTICATION AND USER FUNCTIONS
###################################################

class User:
	def __init__(self, jsonBlob): self.username = jsonBlob["username"]
	# Required flask_login functions.
	def is_admin(self): return self.username == "admin"
	def get_id(self): return self.username
	def is_authenticated(self): return True
	def is_active(self): return True
	def is_anonymous(self): return False
	@classmethod
	def cu(self):
		"""Returns current user's username"""
		return flask_login.current_user.username

def cryptoRandomString():
	''' Generate a cryptographically secure random string for signing/encrypting cookies. '''
	if 'COOKIE_KEY' in globals():
		return COOKIE_KEY
	else:
		return hashlib.md5(str(random.random())+str(time.time())).hexdigest()

login_manager = flask_login.LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login_page"
app.secret_key = cryptoRandomString()

def send_link(email, message, u={}):
	''' Send message to email using Amazon SES. '''
	try:
		key = open("emailCredentials.key").read()
		c = boto.ses.connect_to_region("us-east-1",
			aws_access_key_id="AKIAJLART4NXGCNFEJIQ",
			aws_secret_access_key=key)
		reg_key = hashlib.md5(str(time.time())+str(random.random())).hexdigest()
		u["reg_key"] = reg_key
		u["timestamp"] = dt.datetime.strftime(dt.datetime.now(), format="%c")
		u["registered"] = False
		u["email"] = email
		outDict = c.send_email("admin@omf.coop", "OMF Registration Link",
			message.replace("reg_link", URL+"/register/"+email+"/"+reg_key), [email])
		json.dump(u, open("data/User/"+email+".json", "w"), indent=4)
		return "Success"
	except:
		return "Failed"

@login_manager.user_loader
def load_user(username):
	''' Required by flask_login to return instance of the current user '''
	return User(json.load(open("./data/User/" + username + ".json")))

def generate_csrf_token():
	if "_csrf_token" not in session:
		session["_csrf_token"] = cryptoRandomString()
	return session["_csrf_token"]

app.jinja_env.globals["csrf_token"] = generate_csrf_token

@app.route("/login", methods = ["POST"])
def login():
	''' Authenticate a user and send them to the URL they requested. '''
	username, password, remember = map(request.form.get, ["username",
		"password", "remember"])
	userJson = None
	for u in os.listdir("./data/User/"):
		if u.lower() == username.lower() + ".json":
			userJson = json.load(open("./data/User/" + u))
			break
	if userJson and pbkdf2_sha512.verify(password,
			userJson["password_digest"]):
		user = User(userJson)
		flask_login.login_user(user, remember = remember == "on")
	nextUrl = str(request.form.get("next","/"))
	return redirect(nextUrl)

@app.route("/login_page")
def login_page():
	nextUrl = str(request.args.get("next","/"))
	if flask_login.current_user.is_authenticated():
		return redirect(urlTarget)
	# Generate list of models with quickRun
	modelNames = []
	for modelName in models.__all__:
		thisModel = getattr(models, modelName)
		if hasattr(thisModel, 'quickRender'):
			modelNames.append(modelName)
	if not modelNames:
		modelNames.append("No Models Available")
	return render_template("clusterLogin.html", next=nextUrl, modelNames=modelNames)

@app.route("/logout")
def logout():
	flask_login.logout_user()
	return redirect("/")

@app.route("/deleteUser", methods=["POST"])
@flask_login.login_required
def deleteUser():
	if User.cu() != "admin":
		return "You are not authorized to delete users"
	username = request.form.get("username")
	# Clean up user data.
	try:
		shutil.rmtree("data/Model/" + username)
	except Exception, e:
		print "USER DATA DELETION FAILED FOR", e
	os.remove("data/User/" + username + ".json")
	print "SUCCESFULLY DELETE USER", username
	return "Success"

@app.route("/new_user", methods=["POST"])
# @flask_login.login_required #TODO: REVIEW
def new_user():
	email = request.form.get("email")
	if email == "": return "EMPTY"
	if email in [f[0:-5] for f in os.listdir("data/User")]:
		u = json.load(open("data/User/" + email + ".json"))
		if u.get("password_digest") or not request.form.get("resend"):
			return "Already Exists"
	message = "Click the link below to register your account for the OMF.  This link will expire in 24 hours:\n\nreg_link"
	return send_link(email, message)

@app.route("/forgotpwd", methods=["POST"])
def forgotpwd():
	email = request.form.get("email")
	try:
		user = json.load(open("data/User/" + email + ".json"))
		message = "Click the link below to reset your password for the OMF.  This link will expire in 24 hours.\n\nreg_link"
		return send_link(email, message, user)
	except Exception, e:
		print "ERROR: failed to password reset user", email, "with exception", e
		return "Error"

@app.route("/register/<email>/<reg_key>", methods=["GET", "POST"])
def register(email, reg_key):
	if flask_login.current_user.is_authenticated():
		return redirect("/")
	try:
		user = json.load(open("data/User/" + email + ".json"))
	except Exception:
		user = None
	if not (user and
			reg_key == user.get("reg_key") and
			user.get("timestamp") and
			dt.timedelta(1) > dt.datetime.now() - dt.datetime.strptime(user.get("timestamp"), "%c")):
		return "This page either expired, or you are not supposed to access it. It might not even exist"
	if request.method == "GET":
		return render_template("register.html", email=email)
	password, confirm_password = map(request.form.get, ["password", "confirm_password"])
	if password == confirm_password and request.form.get("legalAccepted","") == "on":
		user["username"] = email
		user["password_digest"] = pbkdf2_sha512.encrypt(password)
		flask_login.login_user(User(user))
		with open("data/User/"+user["username"]+".json","w") as outFile:
			json.dump(user, outFile, indent=4)
	else:
		return "Passwords must both match and you must accept the Terms of Use and Privacy Policy. Please go back and try again."
	return redirect("/")

@app.route("/changepwd", methods=["POST"])
@flask_login.login_required
def changepwd():
	old_pwd, new_pwd, conf_pwd = map(request.form.get, ["old_pwd", "new_pwd", "conf_pwd"])
	user = json.load(open("./data/User/" + User.cu() + ".json"))
	if pbkdf2_sha512.verify(old_pwd, user["password_digest"]):
		if new_pwd == conf_pwd:
			user["password_digest"] = pbkdf2_sha512.encrypt(new_pwd)
			with open("./data/User/" + User.cu() + ".json","w") as outFile:
				json.dump(user, outFile, indent=4)
			return "Success"
		else:
			return "not_match"
	else:
		return "not_auth"

@app.route("/adminControls")
@flask_login.login_required
def adminControls():
	''' Render admin controls. '''
	if User.cu() != "admin":
		return redirect("/")
	users = [{"username":f[0:-5]} for f in safeListdir("data/User")
		if f not in ["admin.json","public.json"]]
	for user in users:
		userDict = json.load(open("data/User/" + user["username"] + ".json"))
		tStamp = userDict.get("timestamp","")
		if userDict.get("password_digest"):
			user["status"] = "Registered"
		elif dt.timedelta(1) > dt.datetime.now() - dt.datetime.strptime(tStamp, "%c"):
			user["status"] = "emailSent"
		else:
			user["status"] = "emailExpired"
	return render_template("adminControls.html", users = users)

@app.route("/myaccount")
@flask_login.login_required
def myaccount():
	''' Render account info for any user. '''
	return render_template("myaccount.html", user=User.cu())

@app.route("/robots.txt")
def static_from_root():
	return send_from_directory(app.static_folder, request.path[1:])

###################################################
# MODEL FUNCTIONS
###################################################

@app.route("/model/<owner>/<modelName>")
@flask_login.login_required
def showModel(owner, modelName):
	''' Render a model template with saved data. '''
	if owner==User.cu() or "admin"==User.cu() or owner=="public":
		modelDir = "./data/Model/" + owner + "/" + modelName
		with open(modelDir + "/allInputData.json") as inJson:
			modelType = json.load(inJson).get("modelType","")
		thisModel = getattr(models, modelType)
		return thisModel.renderTemplate(thisModel.template, modelDir, False, getDataNames())
	else:
		return redirect("/")

@app.route("/newModel/<modelType>/<modelName>", methods=["POST"])
@flask_login.login_required
def newModel(modelType, modelName):
	''' Display the module template for creating a new model. '''
	modelDir = os.path.join(_omfDir, "data", "Model", User.cu(), modelName)
	os.makedirs(modelDir)
	inputDict = {"modelName" : modelName, "modelType" : modelType, "user":User.cu(), "created" : str(dt.datetime.now())}
	if modelType in ['voltageDrop', 'gridlabMulti', 'cvrDynamic', 'cvrStatic', 'solarEngineering']:
		newSimpleFeeder(modelName, 1, False, 'feeder1')
		inputDict['feederName1'] = 'feeder1'
	with open(os.path.join(modelDir, "allInputData.json"),"w") as inputFile:
		json.dump(inputDict, inputFile, indent = 4)
	thisModel = getattr(models, modelType)
	return thisModel.renderTemplate(thisModel.template, modelDir, False, getDataNames())

@app.route("/runModel/", methods=["POST"])
@flask_login.login_required
def runModel():
	''' Start a model running and redirect to its running screen. '''
	pData = request.form.to_dict()
	modelModule = getattr(models, pData["modelType"])
	# Handle the user.
	if User.cu() == "admin" and pData["user"] == "public":
		user = "public"
	elif User.cu() == "admin" and pData["user"] != "public" and pData["user"] != "":
		user = pData["user"].replace('/','')
	else:
		user = User.cu()
	del pData["user"]
	# Handle the model name.
	modelName = pData["modelName"]
	del pData["modelName"]
	modelModule.run(os.path.join(_omfDir, "data", "Model", user, modelName), pData)
	return redirect("/model/" + user + "/" + modelName)

@app.route("/quickNew/<modelType>")
def quickNew(modelType):
	thisModel = getattr(models, modelType)
	if hasattr(thisModel, 'quickRender'):
		return thisModel.quickRender(thisModel.template, datastoreNames=getDataNames())
	else:
		return redirect("/")

@app.route("/quickRun/", methods=["POST"])
def quickRun():
	pData = request.form.to_dict()
	modelModule = getattr(models, pData["modelType"])
	user = pData["quickRunEmail"]
	modelName = "QUICKRUN-" + pData["modelType"]
	modelModule.run(os.path.join(_omfDir, "data", "Model", user, modelName), pData)
	return redirect("/quickModel/" + user + "/" + modelName)

@app.route("/quickModel/<owner>/<modelName>")
def quickModel(owner, modelName):
	''' Render a quickrun model template with saved data. '''
	modelDir = "./data/Model/" + owner + "/" + modelName
	with open(modelDir + "/allInputData.json") as inJson:
		modelType = json.load(inJson).get("modelType","")
	thisModel = getattr(models, modelType)
	return thisModel.quickRender(thisModel.template, modelDir, False, getDataNames())

@app.route("/cancelModel/", methods=["POST"])
@flask_login.login_required
def cancelModel():
	''' Cancel an already running model. '''
	pData = request.form.to_dict()
	modelModule = getattr(models, pData["modelType"])
	modelModule.cancel(os.path.join(_omfDir,"data","Model",pData["user"],pData["modelName"]))
	return redirect("/model/" + pData["user"] + "/" + pData["modelName"])

@app.route("/duplicateModel/<owner>/<modelName>/", methods=["POST"])
@flask_login.login_required
def duplicateModel(owner, modelName):
	newName = request.form.get("newName","")
	if owner==User.cu() or "admin"==User.cu() or "public"==owner:
		destinationPath = "./data/Model/" + User.cu() + "/" + newName
		shutil.copytree("./data/Model/" + owner + "/" + modelName, destinationPath)
		with open(destinationPath + "/allInputData.json","r") as inFile:
			inData = json.load(inFile)
		inData["created"] = str(dt.datetime.now())
		with open(destinationPath + "/allInputData.json","w") as outFile:
			json.dump(inData, outFile, indent=4)
		return redirect("/model/" + User.cu() + "/" + newName)
	else:
		return False

@app.route("/publishModel/<owner>/<modelName>/", methods=["POST"])
@flask_login.login_required
def publishModel(owner, modelName):
	newName = request.form.get("newName","")
	if owner==User.cu() or "admin"==User.cu():
		destinationPath = "./data/Model/public/" + newName
		shutil.copytree("./data/Model/" + owner + "/" + modelName, destinationPath)
		with open(destinationPath + "/allInputData.json","r+") as inFile:
			inData = json.load(inFile)
			inData["created"] = str(dt.datetime.now())
			inFile.seek(0)
			json.dump(inData, inFile, indent=4)
		return redirect("/model/public/" + newName)
	else:
		return False

###################################################
# FEEDER FUNCTIONS
###################################################

def writeToInput(workDir, entry, key):
	try:
		with open(workDir + "/allInputData.json") as inJson:
			allInput = json.load(inJson)
		allInput[key] = entry
		with open(workDir+"/allInputData.json","w") as inputFile:
			json.dump(allInput, inputFile, indent = 4)
	except: return "Failed"

@app.route("/feeder/<owner>/<modelName>/<feederNum>")
@flask_login.login_required
def feederGet(owner, modelName, feederNum):
	''' Editing interface for feeders. '''
	allFeeders = getDataNames()["feeders"]
	modelDir = os.path.join(_omfDir, "data","Model", owner, modelName)
	feederName = json.load(open(modelDir + "/allInputData.json")).get('feederName'+str(feederNum))
	# MAYBEFIX: fix modelFeeder
	return render_template("gridEdit.html", feeders=allFeeders, modelName=modelName, feederName=feederName, feederNum=feederNum, ref=request.referrer, is_admin=User.cu()=="admin", modelFeeder=False, public=owner=="public",
		currUser = User.cu(), owner = owner)

@app.route("/getComponents/")
@flask_login.login_required
def getComponents():
	path = "data/Component/"
	components = {name[0:-5]:json.load(open(path + name)) for name in os.listdir(path)}
	return json.dumps(components)

@app.route("/checkConversion/<feederName>", methods=["POST","GET"])
def checkConversion(feederName):
	owner = User.cu()
	path = ("data/Conversion/" + owner + "/" + feederName + ".json")
	print "Check conversion status:", os.path.exists(path), "for path", path
	return jsonify(exists=os.path.exists(path))

@app.route("/milsoftImport/", methods=["POST"])
@flask_login.login_required
def milsoftImport():
	''' API for importing a milsoft feeder. '''
	modelName = request.form.get("modelName","")
	feederName = str(request.form.get("feederNameM","feeder"))
	feederNum = request.form.get("feederNum",1)
	stdString, seqString = map(lambda x: request.files[x].stream.read(), ["stdFile", "seqFile"])
	if not os.path.isdir("data/Conversion/" + User.cu()):
		os.makedirs("data/Conversion/" + User.cu())
	with open("data/Conversion/" + User.cu() + "/" + feederName + ".json", "w+") as conFile:
		conFile.write("WORKING")
	importProc = Process(target=milImportBackground, args=[User.cu(), modelName, feederName, feederNum, stdString, seqString])
	importProc.start()
	return ('',204)

def milImportBackground(owner, modelName, feederName, feederNum, stdString, seqString):
	''' Function to run in the background for Milsoft import. '''
	modelDir = "data/Model/"+owner+"/"+modelName
	feederDir = modelDir+"/"+feederName+".omd"
	newFeeder = dict(**feeder.newFeederWireframe)
	[newFeeder["tree"], xScale, yScale] = milToGridlab.convert(stdString, seqString)
	newFeeder["layoutVars"]["xScale"] = xScale
	newFeeder["layoutVars"]["yScale"] = yScale
	with open("./schedules.glm","r") as schedFile:
		newFeeder["attachments"] = {"schedules.glm":schedFile.read()}
	try: os.remove(feederDir)
	except: pass
	with open(feederDir, "w") as outFile:
		json.dump(newFeeder, outFile, indent=4)
	os.remove("data/Conversion/" + owner + "/" + feederName + ".json")
	removeFeeder(modelName, feederNum)
	writeToInput(modelDir, feederName, 'feederName'+str(feederNum))

@app.route("/gridlabdImport/", methods=["POST"])
@flask_login.login_required
def gridlabdImport():
	'''This function is used for gridlabdImporting'''
	modelName = request.form.get("modelName","")
	feederName = str(request.form.get("feederNameG",""))
	feederNum = request.form.get("feederNum",1)
	glmString = request.files["glmFile"].stream.read()
	if not os.path.isdir("data/Conversion/" + User.cu()):
		os.makedirs("data/Conversion/" + User.cu())
	with open("data/Conversion/" + User.cu() + "/" + feederName + ".json", "w+") as conFile:
		conFile.write("WORKING")
	importProc = Process(target=gridlabImportBackground, args=[User.cu(), modelName, feederName, feederNum, glmString])
	importProc.start()
	return ('',204)

def gridlabImportBackground(owner, modelName, feederName, feederNum, glmString):
	''' Function to run in the background for Milsoft import. '''
	modelDir = "data/Model/"+owner+"/"+modelName
	feederDir = modelDir+"/"+feederName+".omd"
	newFeeder = dict(**feeder.newFeederWireframe)
	newFeeder["tree"] = feeder.parse(glmString, False)
	newFeeder["layoutVars"]["xScale"] = 0
	newFeeder["layoutVars"]["yScale"] = 0
	with open("./schedules.glm","r") as schedFile:
		newFeeder["attachments"] = {"schedules.glm":schedFile.read()}
	try: os.remove(feederDir)
	except: pass
	with open(feederDir, "w") as outFile:
		json.dump(newFeeder, outFile, indent=4)
	os.remove("data/Conversion/" + owner + "/" + feederName + ".json")
	removeFeeder(modelName, feederNum)
	writeToInput(modelDir, feederName, 'feederName'+str(feederNum))

# TODO: Check if rename mdb files worked
@app.route("/cymeImport/", methods=["POST"])
@flask_login.login_required
def cymeImport():
	''' API for importing a cyme feeder. '''
	modelName = request.form.get("modelName","")
	feederName = str(request.form.get("feederNameC",""))
	feederNum = request.form.get("feederNum",1)
	mdbNetString, mdbEqString = map(lambda x: request.files[x], ["mdbNetFile", "mdbEqFile"])
	if not os.path.isdir("data/Conversion/" + User.cu()):
		os.makedirs("data/Conversion/" + User.cu())
	with open("data/Conversion/" + User.cu() + "/" + feederName + ".json", "w+") as conFile:
		conFile.write("WORKING")
	importProc = Process(target=cymeImportBackground, args=[User.cu(), modelName, feederName, feederNum, mdbNetString.filename, mdbEqString.filename])
	importProc.start()
	return ('',204)

def cymeImportBackground(owner, modelName, feederName, feederNum, mdbNetString, mdbEqString):
	''' Function to run in the background for Milsoft import. '''
	modelDir = "data/Model/"+owner+"/"+modelName
	feederDir = modelDir+"/"+feederName+".omd"
	newFeeder = dict(**feeder.newFeederWireframe)
	[newFeeder["tree"], xScale, yScale] = cymeToGridlab.convertCymeModel(mdbNetString, mdbEqString)
	newFeeder["layoutVars"]["xScale"] = xScale
	newFeeder["layoutVars"]["yScale"] = yScale
	with open("./schedules.glm","r") as schedFile:
		newFeeder["attachments"] = {"schedules.glm":schedFile.read()}
	try: os.remove(feederDir)
	except: pass
	with open(feederDir, "w") as outFile:
		json.dump(newFeeder, outFile, indent=4)
	os.remove("data/Conversion/" + owner + "/" + feederName + ".json")
	removeFeeder(modelName, feederNum)
	writeToInput(modelDir, feederName, 'feederName'+str(feederNum))

@app.route("/newSimpleFeeder/<modelName>/<feederNum>/<writeInput>", methods=["POST", "GET"])
def newSimpleFeeder(modelName, feederNum=1, writeInput=False, feederName='feeder1'):
	workDir = os.path.join(_omfDir, "data", "Model", User.cu(), modelName)
	for i in range(2,6):
		if not os.path.isfile(os.path.join(workDir,feederName+'.omd')):
			with open("./static/SimpleFeeder.json", "r") as simpleFeederFile:
				with open(os.path.join(workDir, feederName+".omd"), "w") as outFile:
					outFile.write(simpleFeederFile.read())
			break
		else: feederName = 'feeder'+str(i)
	if writeInput: writeToInput(workDir, feederName, 'feederName'+str(feederNum))
	return ('Success',204)

@app.route("/newBlankFeeder/", methods=["POST"])
@flask_login.login_required
def newBlankFeeder():
	'''This function is used for creating a new blank feeder.'''
	modelName = request.form.get("modelName","")
	feederName = str(request.form.get("feederNameNew"))
	feederNum = request.form.get("feederNum",1)
	if feederName == '': feederName = 'feeder'
	modelDir = os.path.join(_omfDir, "data","Model", User.cu(), modelName)
	owner = User.cu()
	removeFeeder(modelName, feederNum)
	newSimpleFeeder(modelName, feederNum, False, feederName)
	writeToInput(modelDir, feederName, 'feederName'+str(feederNum))
	return redirect(url_for('feederGet', owner=owner, modelName=modelName, feederNum=feederNum))

@app.route("/feederData/<owner>/<modelName>/<feederName>/")
@app.route("/feederData/<owner>/<modelName>/<feederName>/<modelFeeder>")
@flask_login.login_required
def feederData(owner, modelName, feederName, modelFeeder=False):
	#MAYBEFIX: fix modelFeeder capability.
	if User.cu()=="admin" or owner==User.cu() or owner=="public":
		with open("data/Model/" + owner + "/" + modelName + "/" + feederName + ".omd", "r") as feedFile:
			return feedFile.read()

@app.route("/saveFeeder/<owner>/<modelName>/<feederName>", methods=["POST"])
@flask_login.login_required
def saveFeeder(owner, modelName, feederName):
	''' Save feeder data. '''
	if owner == User.cu() or "admin" == User.cu() or owner=="public":
		with open("data/Model/" + owner + "/" + modelName + "/" + feederName + ".omd", "w") as outFile:
			payload = json.loads(request.form.to_dict().get("feederObjectJson","{}"))
			json.dump(payload, outFile, indent=4)
	return ('Success',204)

@app.route("/renameFeeder/<user>/<modelName>/<oldName>/<feederName>/<feederNum>", methods=["POST"])
@flask_login.login_required
def renameFeeder(user, modelName, oldName, feederName, feederNum):
	''' rename a feeder. '''
	modelDir = os.path.join(_omfDir, "data","Model", user, modelName)
	feederDir = os.path.join(modelDir, feederName+'.omd')
	oldfeederDir = os.path.join(modelDir, oldName+'.omd')
	if not os.path.isfile(feederDir) and os.path.isfile(oldfeederDir):
		with open(oldfeederDir, "r") as feederIn:
			with open(feederDir, "w") as outFile:
				outFile.write(feederIn.read())
	elif os.path.isfile(feederDir): return ('Failure', 204)
	elif not os.path.isfile(oldfeederDir): return ('Failure', 204)
	os.remove(oldfeederDir)
	writeToInput(modelDir, feederName, 'feederName'+str(feederNum))
	return ('Success',204)

@app.route("/removeFeeder/<modelName>/<feederNum>", methods=["GET","POST"])
@app.route("/removeFeeder/<modelName>/<feederNum>/<feederName>", methods=["GET","POST"])
@flask_login.login_required
def removeFeeder(modelName, feederNum, feederName=None):
	'''Remove a feeder from input data.'''
	try:
		modelDir = os.path.join(_omfDir, "data","Model", User.cu(), modelName)
		with open(modelDir + "/allInputData.json") as inJson:
			allInput = json.load(inJson)
		try:
			feederName = str(allInput.get('feederName'+str(feederNum)))
			os.remove(os.path.join(modelDir, feederName +'.omd'))
		except: print "Couldn't remove feeder file in web.removeFeeder()."
		allInput.pop("feederName"+str(feederNum))
		with open(modelDir+"/allInputData.json","w") as inputFile:
			json.dump(allInput, inputFile, indent = 4)
		return ('Success',204)
	except:
		return ('Failed',204)

@app.route("/loadFeeder/<tfeederName>/<tmodelName>/<modelName>/<feederNum>", methods=["GET","POST"])
@flask_login.login_required
def loadFeeder(tfeederName, tmodelName, modelName, feederNum):
	'''Load a feeder from one model to another.'''
	print "Entered loadFeeder with info: tfeederName %s, tmodelName: %s, modelname: %s, feederNum: %s"%(tfeederName, tmodelName, str(modelName), str(feederNum))
	tmodelDir = "./data/Model/" + User.cu() + "/" + tmodelName
	modelDir = "./data/Model/" + User.cu() + "/" + modelName	
	with open(modelDir + "/allInputData.json") as inJson:
		feederName = allInput = json.load(inJson).get('feederName'+str(feederNum))
	with open(os.path.join(tmodelDir, tfeederName+'.omd'), "r") as inFeeder:
		with open(os.path.join(modelDir, feederName+".omd"), "w") as outFile:
			outFile.write(inFeeder.read())
	writeToInput(modelDir, feederName, 'feederName'+str(feederNum))
	return redirect(url_for('feederGet', owner=User.cu(), modelName=modelName, feederNum=feederNum))

###################################################
# OTHER FUNCTIONS
###################################################

@app.route("/")
@flask_login.login_required
def root():
	''' Render the home screen of the OMF. '''
	# Gather object names.
	publicModels = [{"owner":"public","name":x} for x in safeListdir("data/Model/public/")]
	userModels = [{"owner":User.cu(), "name":x} for x in safeListdir("data/Model/" + User.cu())]
	allModels = publicModels + userModels
	# Allow admin to see all models.
	isAdmin = User.cu() == "admin"
	if isAdmin:
		allModels = [{"owner":owner,"name":mod} for owner in safeListdir("data/Model/")
			for mod in safeListdir("data/Model/" + owner)]
	# Grab metadata for models.
	for mod in allModels:
		try:
			modPath = "data/Model/" + mod["owner"] + "/" + mod["name"]
			allInput = json.load(open(modPath + "/allInputData.json"))
			mod["runTime"] = allInput.get("runTime","")
			mod["modelType"] = allInput.get("modelType","")
			mod["status"] = getattr(models, mod["modelType"]).getStatus(modPath)
			# mod["created"] = allInput.get("created","")
			mod["editDate"] = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(os.stat(modPath).st_ctime))
		except:
			continue
	return render_template("home.html", models = allModels, current_user = User.cu(), is_admin = isAdmin, modelNames = models.__all__)

@app.route("/delete/<objectType>/<owner>/<objectName>", methods=["POST"])
@flask_login.login_required
def delete(objectType, objectName, owner):
	''' Delete models or feeders. '''
	if owner != User.cu() and User.cu() != "admin":
		return False
	if objectType == "Feeder":
		os.remove("data/Model/" + owner + "/" + objectName + "/" + "feeder.omd")
		return redirect("/#feeders")
	elif objectType == "Model":
		shutil.rmtree("data/Model/" + owner + "/" + objectName)
	return redirect("/")

@app.route("/downloadModelData/<owner>/<modelName>/<path:fullPath>")
@flask_login.login_required
def downloadModelData(owner, modelName, fullPath):
	pathPieces = fullPath.split('/')
	return send_from_directory("data/Model/"+owner+"/"+modelName+"/"+"/".join(pathPieces[0:-1]), pathPieces[-1])

@app.route("/uniqObjName/<objtype>/<name>")
@app.route("/uniqObjName/<objtype>/<name>/<modelName>")
@flask_login.login_required
def uniqObjName(objtype, name, modelName=False):
	''' Checks if a given object type/owner/name is unique. '''
	owner = User.cu()
	if objtype == "Model":
		path = "data/Model/" + owner + "/" + name
	elif objtype == "Feeder":
		path = "data/Model/" + owner + "/" + modelName + "/" + name + ".omd"
		if name == 'feeder': return jsonify(exists=True)
	return jsonify(exists=os.path.exists(path))

if __name__ == "__main__":
	URL = "http://localhost:5000"
	template_files = ["templates/"+ x  for x in os.listdir("templates")]
	model_files = ["models/" + x for x in os.listdir("models")]
	app.run(debug=True, extra_files=template_files + model_files)
