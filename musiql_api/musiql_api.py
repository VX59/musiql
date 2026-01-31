from pydantic import BaseModel, HttpUrl
from fastapi import APIRouter, Depends
from db import get_db
from models import MusiqlRepository
from yt_dlp import YoutubeDL
import random
import os
from sqlalchemy.orm import Session

router = APIRouter()

class MusiqlPayload(BaseModel):
    resource_url: HttpUrl

def download_resource(resrouce_url:HttpUrl) -> tuple[dict, int]:
    uri = random.randint(0,1000000)
    filename = f"{uri}"
    out_path = os.path.join("/home/jacob/musiql/staging_dump", filename)

    ydl_opts = {
        'outtmpl': out_path,
        'format': 'bestaudio/best',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
    }
    with YoutubeDL(ydl_opts) as ydl: 
        ydl.download([str(resrouce_url)])

        return ydl.extract_info(str(resrouce_url)), uri

@router.post("/musiql")
async def receive_music(payload: MusiqlPayload, db: Session = Depends(get_db)):

    info, uri = download_resource(payload.resource_url)
    path = f"/home/jacob/musiql/staging_dump/{uri}.mp3" # yikes
    with open(path, "rb") as reader:
        resource_bytes = reader.read()
    
    os.remove(path)

    new_resource = MusiqlRepository(
        title=info.get('title'), # the youtube video name
        artists=[info.get('uploader')], # from youtube not the artist -- fix later
        data=resource_bytes,
    )

    db.add(new_resource)
    db.commit()

    return {"status": "ok"}