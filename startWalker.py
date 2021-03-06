import time
from datetime import datetime
from threading import Thread, Event
import logging
from colorlog import ColoredFormatter
from walkerArgs import parseArgs
import sys
import os
import math
from watchdog.observers import Observer
from watchdog.events import PatternMatchingEventHandler
from shutil import copyfile
from copyMons import MonRaidImages
from fileObserver import checkScreenshot
import heapq
from multiprocessing import Process

from routecalc.calculate_route import getJsonRoute, getDistanceOfTwoPointsInMeters
from vnc.vncWrapper import VncWrapper
from telnet.telnetGeo import TelnetGeo
from telnet.telnetMore import TelnetMore
from ocr.pogoWindows import PogoWindows
from dbWrapper import *

from ocr.pogoWindows import PogoWindows
import collections


RaidLocation = collections.namedtuple('RaidLocation', ['latitude', 'longitude'])


class LogFilter(logging.Filter):

    def __init__(self, level):
        self.level = level

    def filter(self, record):
        return record.levelno < self.level

console = logging.StreamHandler()
args = parseArgs()
sleep = False
nextRaidQueue = []

if not (args.verbose):
    console.setLevel(logging.INFO)

formatter = ColoredFormatter(
    '%(log_color)s [%(asctime)s] [%(threadName)16s] [%(module)14s]' +
    ' [%(levelname)8s] %(message)s',
    datefmt='%m-%d %H:%M:%S',
    reset=True,
    log_colors={
        'DEBUG': 'purple',
        'INFO': 'cyan',
        'WARNING': 'yellow',
        'ERROR': 'red',
        'CRITICAL': 'red,bg_white',
    },
    secondary_log_colors={},
    style='%'
    )

console.setFormatter(formatter)

# Redirect messages lower than WARNING to stdout
stdout_hdlr = logging.StreamHandler(sys.stdout)
stdout_hdlr.setFormatter(formatter)
log_filter = LogFilter(logging.WARNING)
stdout_hdlr.addFilter(log_filter)
stdout_hdlr.setLevel(5)

# Redirect messages equal or higher than WARNING to stderr
stderr_hdlr = logging.StreamHandler(sys.stderr)
stderr_hdlr.setFormatter(formatter)
stderr_hdlr.setLevel(logging.WARNING)

log = logging.getLogger()
log.addHandler(stdout_hdlr)
log.addHandler(stderr_hdlr)

telnMore = None
if not args.only_ocr:
    print("Starting Telnet MORE Client")
    telnMore = TelnetMore(str(args.tel_ip), args.tel_port, str(args.tel_password))



def main():
    log.info("Starting TheRaidMap")
    sys.excepthook = handle_exception
    log.info("Parsing arguments")
    args = parseArgs()
    set_log_and_verbosity(log)
    dbWrapper = DbWrapper(str(args.dbip), args.dbport, args.dbusername, args.dbpassword, args.dbname, args.timezone)

    if args.clean_hash_database:
        log.info('Cleanup Hash Database')
        dbWrapper.deleteHashTable('999', '')

    if not os.path.exists(args.raidscreen_path):
        log.info('Raidscreen directory created')
        os.makedirs(args.raidscreen_path)


    dbWrapper.createHashDatabaseIfNotExists()

    MonRaidImages.runAll(args.pogoasset)

    if not args.only_ocr:
        log.info('Starting Scanning Thread....')
        t = Thread(target=main_thread, name='main')
        t.daemon = True
        t.start()

    if not args.only_scan:
        #if args.ocr_multitask:
        #    import multiprocessing
        #    p = multiprocessing.Process(target=observer, name='OCR-Process', args=(args.raidscreen_path, args.screen_width, args.screen_height,))
        #    p.daemon = True
        #    p.start()
        #else:
        #    log.info('Starting OCR Thread....')
        #    t_observ = Thread(name='observer', target=observer(args.raidscreen_path, args.screen_width, args.screen_height))
        #    t_observ.daemon = True
        #    t_observ.start()
        log.info('Starting OCR Thread....')
        t_observ = Thread(name='observer', target=observer(args.raidscreen_path, args.screen_width, args.screen_height))
        t_observ.daemon = True
        t_observ.start()
        #param = str(args.raidscreen_path)
        #process = Process(target=observer, args=(param,))
        #process.daemon = True
        #process.start();


    if args.sleeptimer:
        log.info('Starting Sleeptimer....')
        t_sleeptimer = Thread(name='sleeptimer', target=sleeptimer(args.sleepinterval))
        t_sleeptimer.daemon = True
        t_sleeptimer.start()

    while True:
        time.sleep(10)

def sleeptimer(sleeptime):
    global sleep
    global telnMore
    tmFrom = datetime.datetime.strptime(sleeptime[0],"%H:%M")
    log.debug("tmFrom: %s" % str(tmFrom))
    tmTil = datetime.datetime.strptime(sleeptime[1],"%H:%M") + datetime.timedelta(hours = 24)
    log.debug("tmTil: %s" % str(tmTil))
    while True:
        #we assume sleep is always at night...
        tmNow = datetime.datetime.strptime(datetime.datetime.now().strftime('%H:%M'),"%H:%M")
        tmNowNextDay = tmNow + datetime.timedelta(hours = 24)
        #log.debug("tmNow: %s" % str(tmNow))

        if tmNow >= tmFrom and tmNowNextDay < tmTil:
            log.info('Going to sleep - byebye')
            #Stopping pogo...
            if telnMore:
                telnMore.stopApp("com.nianticlabs.pokemongo")
            sleep = True

            while sleep:
                tmNow = datetime.datetime.strptime(datetime.datetime.now().strftime('%H:%M'),"%H:%M")
                tmNowNextDay = tmNow + datetime.timedelta(hours = 24)
                log.debug('Still sleeping, current time... %s' % str(tmNow))
                if tmNow < tmFrom and tmNowNextDay >= tmTil:
                    log.warning('Wakeup - here we go ...')
                    #Turning screen on and starting app
                    if telnMore:
                        telnMore.turnScreenOn()
                        telnMore.startApp("com.nianticlabs.pokemongo")
                    sleep = False
                    break
                time.sleep(1)
        time.sleep(1)

def handle_exception(exc_type, exc_value, exc_traceback):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return

    log.error("Uncaught exception", exc_info=(
        exc_type, exc_value, exc_traceback))

def set_log_and_verbosity(log):
    # Always write to log file.
    args = parseArgs()
    # Create directory for log files.
    if not os.path.exists(args.log_path):
        os.mkdir(args.log_path)
    if not args.no_file_logs:
        filename = os.path.join(args.log_path, args.log_filename)
        filelog = logging.FileHandler(filename)
        filelog.setFormatter(logging.Formatter(
            '%(asctime)s [%(threadName)18s][%(module)14s][%(levelname)8s] ' +
            '%(message)s'))
        log.addHandler(filelog)

    if args.verbose:
            log.setLevel(logging.DEBUG)
    else:
            log.setLevel(logging.INFO)

def printHi():
    log.error("Finished analyzing screenshot")

#to be called regularly... like every 5mins? no idea... would be nicer to simply insert updates
def updateRaidQueue(dbWrapper):
    log.info("Updating raid queue")
    newQueue = dbWrapper.getNextRaidHatches()
    heapq.heapify(newQueue)
    mergeRaidQueue(newQueue)

def mergeRaidQueue(newQueue):
    global nextRaidQueue
    merged = list(set(newQueue + nextRaidQueue))
    heapq.heapify(merged)
    nextRaidQueue = merged
    log.info("Raidqueue: %s" % nextRaidQueue)

def restartPogo():
    global telnMore
    global lastPogoRestart
    curTime = time.time()
    successfulStop = telnMore.stopApp("com.nianticlabs.pokemongo")
    #TODO: errorhandling if it returned false, maybe try again next round?
    #TODO: check if pogo was closed...
    if successfulStop:
        time.sleep(5)
        if telnMore.startApp("com.nianticlabs.pokemongo"):
            log.warning("Starting pogo...")
            time.sleep(60)
            lastPogoRestart = curTime
        #TODO: handle login screen... ?

def turnScreenOnAndStartPogo():
    global telnMore
    if not telnMore.isScreenOn():
        telnMore.startApp("de.grennith.rgc.remotegpscontroller")
        log.warning("Turning screen on")
        telnMore.turnScreenOn()
        time.sleep(10)
    #check if pogo is running and start it if necessary
    if not telnMore.isPogoTopmost():
        log.warning("Starting Pogo")
        telnMore.startApp("com.nianticlabs.pokemongo")
        time.sleep(60)

def main_thread():
    global nextRaidQueue
    global lastPogoRestart
    global telnMore
    global sleep
    log.info("Starting VNC client")
    vncWrapper = VncWrapper(str(args.vnc_ip,), 1, args.vnc_port, args.vnc_password)
    log.info("Starting TelnetGeo Client")
    telnGeo = TelnetGeo(str(args.tel_ip), args.tel_port, str(args.tel_password))
    #log.info("Starting Telnet MORE Client")
    #telnMore = TelnetMore(str(args.tel_ip), args.tel_port, str(args.tel_password))
    log.info("Starting pogo window manager")
    pogoWindowManager = PogoWindows(str(args.vnc_ip,), 1, args.vnc_port, args.vnc_password, args.screen_width, args.screen_height, args.temp_path)
    log.info("Starting dbWrapper")
    dbWrapper = DbWrapper(str(args.dbip), args.dbport, args.dbusername, args.dbpassword, args.dbname, args.timezone)
    updateRaidQueue(dbWrapper)

    route = getJsonRoute(args.file)
    lastPogoRestart = time.time()
    lastRaidQueueUpdate = time.time()
    log.info("Route to be taken: %s, amount of coords: %s" % (str(route), str(len(route))))
    #sys.exit(0)
    log.info("Max_distance before teleporting: %s" % args.max_distance)
    log.info("Checking if screen is on and pogo is running")

    if not sleep:
        turnScreenOnAndStartPogo()
    #sys.exit(0)
    while True:
        log.info("Next round")
        lastLat = 0.0
        lastLng = 0.0
        curLat = 0.0
        curLng = 0.0
        #loop over gyms:
        #walk to next gym
        #check errors (anything not raidscreen)
        #get to raidscreen (with the above command)
        #take screenshot and store coords in exif with it
        #check time to restart pogo and reset google play services
        i = 0 #index, iterating with it to either get to the next gym or the priority of our queue
        failcount = 0
        while i < len(route):
            while sleep:
                time.sleep(1)
                #TODO: check if not sleep -> start pogo, if sleep, stop it
            curTime = time.time()
            #update the raid queue every 5mins...
            if (curTime - lastRaidQueueUpdate) >= (5 * 60):
                updateRaidQueue(dbWrapper)
                lastRaidQueueUpdate = curTime

            #we got the latest raids. To avoid the mobile from killing apps,
            #let's restart pogo every 2hours or whatever TODO: consider args
            log.debug("Current time - lastPogoRestart: %s" % str(curTime - lastPogoRestart))
            if (curTime - lastPogoRestart >= (120 * 60)):
                restartPogo()

            lastLat = curLat
            lastLng = curLng
            log.debug("Checking for raidqueue priority. Current time: %s, Current queue: %s" % (str(time.time()), str(nextRaidQueue)))
            #determine whether we move to the next gym or to the top of our priority queue
            if (len(nextRaidQueue) > 0 and nextRaidQueue[0][0] < time.time()):
                #the topmost item in the queue lays in the past...
                log.info('An egg has hatched, get there asap. Location: %s' % str(nextRaidQueue[0]))
                nextStop = heapq.heappop(nextRaidQueue)[1] #gets the location tuple
                curLat = nextStop.latitude
                curLng = nextStop.longitude
                time.sleep(1)
            else:
                #continue as usual
                log.info('Moving on with gym at %s' % route[i])
                curLat = route[i]['lat']
                curLng = route[i]['lng']
                #remove whitespaces that might be on either side...
                #curLat = curLat.strip()
                #curLng = curLng.strip()
                i += 1

            log.debug("next stop: %s, %s" % (str(curLat), str(curLng)))
            log.debug('LastLat: %s, LastLng: %s, CurLat: %s, CurLng: %s' % (lastLat, lastLng, curLat, curLng))
            #get the distance from our current position (last) to the next gym (cur)
            distance = getDistanceOfTwoPointsInMeters(float(lastLat), float(lastLng), float(curLat), float(curLng))
            log.info('Moving %s meters to the next position' % distance)
            if (args.speed == 0 or
                (args.max_distance and distance > args.max_distance)
                    or (lastLat == 0.0 and lastLng == 0.0)):
                log.info("Teleporting...")
                telnGeo.setLocation(curLat, curLng, 0)
                time.sleep(4)
            else:
                log.info('Walking...')
                telnGeo.walkFromTo(lastLat, lastLng, curLat, curLng, args.speed)
                time.sleep(2)

            #ok, we should be at the next gym, check for errors and stuff
            #TODO: improve errorhandling by checking results and trying again and again
            #not using continue to always take a new screenshot...
            #time.sleep(5)

            log.info("Attempting to retrieve screenshot before checking windows")
            if (not vncWrapper.getScreenshot('screenshot.png')):
                log.error("Failed retrieving screenshot before checking windows")
                break
                #failcount += 1
                #TODO: consider proper errorhandling?
                #even restart entire thing? VNC dead means we won't be using the device
                #maybe send email? :D
                #break;
            attempts = 0
            while (not pogoWindowManager.checkRaidscreen('screenshot.png', 123)):
                if (attempts >= 15):
                    #weird count of failures... stop pogo, wait 5mins and try again, could be PTC login issue
                    telnMore.stopApp("com.nianticlabs.pokemongo")
                    time.sleep(360)
                    turnScreenOnAndStartPogo()
                    #restartPogo()
                    attempts = 0
                #not using continue since we need to get a screen before the next round... TODO: consider getting screen for checkRaidscreen within function
                found =  pogoWindowManager.checkPostLoginOkButton('screenshot.png', 123)
                if not found and pogoWindowManager.checkCloseExceptNearbyButton('screenshot.png', 123):
                    log.info("Found close button (X) on a window other than nearby")
                    found = True
                if not found and pogoWindowManager.checkSpeedwarning('screenshot.png', 123):
                    log.info("Found speed warning")
                    found = True
                if not found and pogoWindowManager.checkPostLoginNewsMessage('screenshot.png', 123):
                    log.info("Found post login news message")
                    found = True
                if not found and pogoWindowManager.checkGameQuitPopup('screenshot.png', 123):
                    log.info("Found game quit popup")
                    found = True

                log.info("Previous checks found popups: %s" % str(not found))
                if not found:
                    log.info("Previous checks found nothing. Checking nearby open")
                    pogoWindowManager.checkNearby('screenshot.png', 123)
                try:
                    log.info("Attempting to retrieve screenshot checking windows")
                    vncWrapper.getScreenshot('screenshot.png')
                except:
                    log.error("Failed getting screenshot while checking windows")
                    #failcount += 1
                    #TODO: consider proper errorhandling?
                    #even restart entire thing? VNC dead means we won't be using the device
                    #maybe send email? :D
                    break;

                vncWrapper.getScreenshot('screenshot.png')

                #TODO: take screenshot of raidscreen?
                #we should now see the raidscreen, let's take a screenshot of it
                time.sleep(1)
                attempts += 1
            log.info("Saving raid screenshot")
            countOfRaids = pogoWindowManager.readAmountOfRaids('screenshot.png', 123)
            if countOfRaids > 0:
                curTime = time.time()
                copyfile('screenshot.png', args.raidscreen_path
                    + '/raidscreen_' + str(curTime) + "_" + str(countOfRaids) + '.png')


def observer(scrPath, width, height):
        observer = Observer()
        observer.schedule(checkScreenshot(width, height), path=scrPath)
        observer.start()



if __name__ == '__main__':
    main()
