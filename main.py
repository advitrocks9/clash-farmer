import re
import time
import threading
import os
import random
import numpy as np
import cv2 as cv
import easyocr
from mss import mss
from makcu import create_controller as createController, MouseButton

# ---------- constants ----------

os.makedirs('debug', exist_ok=True)

SLEEP_VERY_SHORT = (0.02, 0.04)
SLEEP_SHORT = (0.05, 0.20)
SLEEP_MEDIUM = (0.20, 0.80)
SLEEP_LONG = (0.80, 2.00)

coords = {
    "elixir": (1621, 129, 1810, 180),
    "gold": (1621, 50, 1810, 101),
    "trophies": (213, 138, 300, 201),
    "attack": (141, 859, 279, 997),
    "damage": (864, 428, 1133, 521),
    "attack_trophies": (1097, 683, 1379, 745),
    "return_home": (876, 817, 1121, 919),
    "start_end": (918, 38, 1082, 74),
    "error": (804, 296, 1208, 335),
    "ultimate": (417, 824, 456, 851),
    "find_match": (1259, 630, 1595, 738),
    "exit_button": (1565, 102, 1619, 159),
    "collect_elixer": (1310, 829, 1517, 910),
    "check_builder_base": (1079, 53, 1169, 101),
    "check_home_base": (985, 60, 1054, 96),
    "center": (971, 408, 1160, 567),
    "troop_deploy": np.array([[152, 553], [863, 41], [1015, 41], [1682, 553], [1318, 843], [480, 839], [479, 798]]),
    "enemy_base": np.array([[525, 522], [917, 241], [1370, 559], [972, 841], [811, 837]]),
    "boosts": np.array([[130, 739], [820, 739], [817, 819], [131, 819]])
}

resources = {
    "dark_elixer": "images/dark_elixer.png",
    "gold": "images/gold.png",
    "elixer": "images/elixer.png",
    "builder_elixer": "images/builder_elixer.png",
    "builder_gold": "images/builder_gold.png",
    "builder_gems": "images/builder_gems.png",
    "builder_reward": "images/builder_reward.png"
}

lowerRed1 = np.array([0, 100, 100])
upperRed1 = np.array([10, 255, 255])
lowerRed2 = np.array([160, 100, 100])
upperRed2 = np.array([179, 255, 255])

# ---------- globals ----------

latestFrame = None
mouse = None
reader = None
statsPrev = None
totalElixirGain = 0
totalGoldGain = 0
attackCount = 0
attackStateLast = None

# ---------- helper ----------

def randomSleep(low, high):
    time.sleep(random.uniform(low, high))

def captureLoop():
    global latestFrame
    with mss() as sct:
        monitor = sct.monitors[1]
        while True:
            sctImg = sct.grab(monitor)
            latestFrame = np.array(sctImg)[:, :, :3]
            time.sleep(0.025)

def getLatestFrame():
    return None if latestFrame is None else latestFrame.copy()

def initOcrReader():
    global reader
    reader = easyocr.Reader(['en'])

threading.Thread(target=initOcrReader, daemon=True).start()

# ---------- vision ----------

def ocrRegion(region, debug=False):
    while reader is None:
        time.sleep(0.05)
    frame = getLatestFrame()
    if frame is None:
        return ''
    l, t, r, b = region
    cropped = frame[t:b, l:r]
    if region == coords["error"]:
        hsv = cv.cvtColor(cropped, cv.COLOR_BGR2HSV)
        mask1 = cv.inRange(hsv, lowerRed1, upperRed1)
        mask2 = cv.inRange(hsv, lowerRed2, upperRed2)
        redMask = cv.bitwise_or(mask1, mask2)
        processed = np.full(cropped.shape, 255, dtype=np.uint8)
        processed[redMask > 0] = [0, 0, 0]
    else:
        processed = cropped
    if debug:
        cv.imwrite(os.path.join('debug', f'ocr_{time.strftime("%Y%m%d_%H%M%S")}.png'), processed)
    upscaled = cv.resize(processed, None, fx=4, fy=4, interpolation=cv.INTER_CUBIC)
    results = reader.readtext(upscaled)
    return ''.join([res[1] for res in results]).strip()

def findBoxes(imgPath, threshold=0.70, debug=False):
    frame = getLatestFrame()
    if frame is None:
        return []
    tpl = cv.imread(imgPath, cv.IMREAD_COLOR)
    if tpl is None:
        return []
    h, w = tpl.shape[:2]
    res = cv.matchTemplate(frame, tpl, cv.TM_CCOEFF_NORMED)
    loc = np.where(res >= threshold)
    rects = [[x, y, w, h] for y, x in zip(*loc)]
    if len(rects) == 0:
        return []
    rects, _ = cv.groupRectangles(rects * 2, 1, 0.5)
    if debug and rects:
        vis = frame.copy()
        for x, y, w, h in rects:
            cv.rectangle(vis, (x, y), (x + w, y + h), (0, 255, 0), 2)
        cv.imwrite(os.path.join('debug', f'boxes_{os.path.basename(imgPath)}_{time.time()}.png'), vis)
    return rects

def randomPointInRegion(acceptedRegion, deniedRegions=None):
    if isinstance(acceptedRegion, (tuple, list)) and len(acceptedRegion) == 4 and not hasattr(acceptedRegion[0], "__iter__"):
        l, t, r, b = acceptedRegion
        while True:
            x = random.randint(l, r)
            y = random.randint(t, b)
            if deniedRegions:
                insideDenied = False
                for dr in deniedRegions:
                    dPts = np.array(dr, dtype=np.int32)
                    if cv.pointPolygonTest(dPts, (x, y), False) >= 0:
                        insideDenied = True
                        break
                if insideDenied:
                    continue
            return x, y
    pts = np.array(acceptedRegion, dtype=np.int32)
    xs, ys = pts[:, 0], pts[:, 1]
    minX, maxX = xs.min(), xs.max()
    minY, maxY = ys.min(), ys.max()
    while True:
        x = random.randint(minX, maxX)
        y = random.randint(minY, maxY)
        if cv.pointPolygonTest(pts, (x, y), False) >= 0:
            if deniedRegions:
                insideDenied = False
                for dr in deniedRegions:
                    dPts = np.array(dr, dtype=np.int32)
                    if cv.pointPolygonTest(dPts, (x, y), False) >= 0:
                        insideDenied = True
                        break
                if insideDenied:
                    continue
            return x, y

def randomPointFromBoxes(boxes):
    x, y, w, h = random.choice(boxes)
    return random.randint(x, x + w - 1), random.randint(y, y + h - 1)

# ---------- interaction ----------

def moveAndClick(x, y, button=MouseButton.LEFT):
    mouse.smooth_move(x, y)
    mouse.click_human_like(button)

def click(templatePath=None, region=None, delay=SLEEP_VERY_SHORT, debug=False):
    if templatePath:
        boxes = findBoxes(templatePath, debug=debug)
        if len(boxes) == 0:
            return False
        x, y = randomPointFromBoxes(boxes)
    elif region:
        x, y = randomPointInRegion(region)
    else:
        return False
    moveAndClick(x, y)
    randomSleep(*delay)
    return True

def clickBoxes(boxes, delay=SLEEP_VERY_SHORT):
    if len(boxes) == 0:
        return False
    x, y = randomPointFromBoxes(boxes)
    moveAndClick(x, y)
    randomSleep(*delay)
    return True

def checkStats():
    elixirTxt = ocrRegion(coords["elixir"])
    goldTxt = ocrRegion(coords["gold"])
    trophyTxt = ocrRegion(coords["trophies"])
    try:
        elixirNum = int(re.sub(r'\D+', '', elixirTxt))
    except:
        elixirNum = 0
    try:
        goldNum = int(re.sub(r'\D+', '', goldTxt))
    except:
        goldNum = 0
    try:
        trophyNum = int(re.sub(r'\D+', '', trophyTxt))
    except:
        trophyNum = 0
    print(f"[Stats] Elixir: {elixirNum}, Gold: {goldNum}, Trophies: {trophyNum}")
    return elixirNum, goldNum, trophyNum

# ---------- attack ----------

def findMatch():
    click(region=coords["attack"], delay=SLEEP_SHORT)
    click(region=coords["find_match"], delay=SLEEP_VERY_SHORT)

def attackOngoing():
    global attackStateLast
    txt = ocrRegion(coords["start_end"]).lower()
    ongoing = "start" not in txt
    if attackStateLast is None or ongoing != attackStateLast:
        attackStateLast = ongoing
    return ongoing

def attackEnd(goHome=False):
    boxes = findBoxes("images/return_home.png")
    ended = len(boxes) > 0
    if ended and goHome:
        clickBoxes(boxes)
    return ended

def useUltimate():
    boxes = findBoxes("images/ultimate.png")
    if len(boxes) != 0:
        randomSleep(*SLEEP_LONG)
        mouse.click_human_like(MouseButton.MIDDLE)

def deployHero(button=MouseButton.LEFT):
    def heroDeployed():
        return any(w in ocrRegion(coords["error"]).lower() for w in ("select", "different", "unit"))
    mouse.click_human_like(MouseButton.MIDDLE)
    while not heroDeployed():
        x, y = randomPointInRegion(coords["troop_deploy"], (coords["enemy_base"], coords["boosts"]))
        moveAndClick(x, y)
        randomSleep(*SLEEP_VERY_SHORT)
        mouse.click_human_like(button)

def deployTroops(phase, button=MouseButton.LEFT):
    def allDeployed():
        return any(w in ocrRegion(coords["error"]).lower() for w in ("all", "forces", "deployed"))
    if phase == 1:
        mouse.click_human_like(MouseButton.MOUSE4)
    elif phase == 2:
        mouse.click_human_like(MouseButton.MOUSE5)
    while not allDeployed():
        x, y = randomPointInRegion(coords["troop_deploy"], (coords["enemy_base"], coords["boosts"]))
        moveAndClick(x, y)
        randomSleep(*SLEEP_VERY_SHORT)
        mouse.click_human_like(button)

def handleBattle():
    randomSleep(*SLEEP_LONG)
    randomSleep(*SLEEP_SHORT)
    roundPhase = 1
    matchStart = time.time()
    while not attackEnd():
        mouse.smooth_scroll(-30)
        if not attackOngoing():
            deployHero()
            deployTroops(roundPhase)
        while attackOngoing() and not attackEnd():
            useUltimate()
            randomSleep(*SLEEP_VERY_SHORT)
        if roundPhase == 1 and time.time() - matchStart > 60:
            roundPhase = 2
        randomSleep(*SLEEP_VERY_SHORT)
    attackEnd(goHome=True)
    randomSleep(*SLEEP_MEDIUM)
