import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import boto3

REGION = "us-east-1"
ROOT = Path(__file__).parent
BUILD = ROOT / "build"

# What a listener should be able to write down after hearing the episode.
TERMS = ["mutual tls", "aws", "alb", "s3", "spiffy", "execute api",
         "403", "api gateway", "cloud watch", "private ca"]

bucket = subprocess.run(
    ["terraform", "-chdir=" + str(ROOT / "terraform"), "output", "-raw", "bucket"],
    check=True, capture_output=True, text=True).stdout.strip()

session = boto3.Session(region_name=REGION)
s3 = session.client("s3")
transcribe = session.client("transcribe")

stamp = str(int(time.time()))
jobs = {}
for name in sys.argv[1:] or sorted(p.name for p in BUILD.iterdir() if p.is_dir()):
    episode = BUILD / name / "episode.mp3"
    s3.upload_file(str(episode), bucket, name + ".mp3")
    job = "polly-podcast-%s-%s" % (name, stamp)
    transcribe.start_transcription_job(
        TranscriptionJobName=job,
        LanguageCode="en-US",
        MediaFormat="mp3",
        Media={"MediaFileUri": "s3://%s/%s.mp3" % (bucket, name)},
    )
    jobs[name] = job
    print("started %s" % job)

for name, job in jobs.items():
    while True:
        result = transcribe.get_transcription_job(TranscriptionJobName=job)["TranscriptionJob"]
        if result["TranscriptionJobStatus"] != "IN_PROGRESS":
            break
        time.sleep(10)
    uri = result["Transcript"]["TranscriptFileUri"]
    text = json.loads(urllib.request.urlopen(uri).read())["results"]["transcripts"][0]["transcript"]
    (BUILD / name / "transcript.txt").write_text(text)
    lowered = text.lower()
    print("\n%s" % name)
    for term in TERMS:
        print("  %-14s %s" % (term, "heard" if term in lowered else "MISSED"))
    transcribe.delete_transcription_job(TranscriptionJobName=job)
    s3.delete_object(Bucket=bucket, Key=name + ".mp3")
