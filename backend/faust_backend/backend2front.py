import queue
FrontEndTaskQueue = queue.Queue()
def FrontEndSay(text):
    FrontEndTaskQueue.put("SAY "+text)
def FrontEndPlayMusic(url):
    FrontEndTaskQueue.put("PLAYMUSIC "+url)
def FrontEndPlayBG(url):
    FrontEndTaskQueue.put("PLAYBG "+url)
def popFrontEndTask():
    try:
        task=FrontEndTaskQueue.get_nowait()
        return task
    except queue.Empty:
        return ""