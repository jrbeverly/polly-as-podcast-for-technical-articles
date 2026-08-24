import hashlib
import json
import subprocess
import sys
from pathlib import Path

import boto3

REGION = "us-east-1"
ROOT = Path(__file__).parent
OUT = ROOT / "build" / "profiles"
SRC = OUT / "src"
TARGET_LUFS = -16.0

HP = "highpass=f=85"
MUD = "equalizer=f=300:width_type=q:w=1.2:g=-3"
DEESS = "deesser=i=0.30:m=0.5:f=0.5"
PRES = "equalizer=f=3500:width_type=q:w=0.9:g=2.5"
AIR = "highshelf=f=9000:g=2.5"
COMP = "acompressor=threshold=-20dB:ratio=2:attack=15:release=250:makeup=1"
ROOM = "aecho=1:0.9:17|29:0.05|0.03"

# Chosen by ear. Both chains share ROOM so the two voices sit in one space.
EXPERT = {"voice": "Tiffany", "engine": "generative",
          "chain": [HP, MUD, DEESS, PRES, AIR, COMP, ROOM]}
MALE = [HP, DEESS, ROOM]

VOICES = {
    "tiffany": EXPERT,
    "stephen": {"voice": "Stephen", "engine": "generative", "chain": MALE},
    "matthew": {"voice": "Matthew", "engine": "generative", "chain": MALE},
    "brian": {"voice": "Brian", "engine": "generative", "chain": MALE},
    "gregory": {"voice": "Gregory", "engine": "long-form", "chain": MALE},
    "patrick": {"voice": "Patrick", "engine": "long-form", "chain": MALE},
}

SHAPED = ("Okay, so, passthrough. If the load balancer isn't validating anything, what am I "
          "actually getting out of it? Because honestly, that sounds strictly worse to me.")
TERSE = "Meaning the load balancer, or the service itself. And the other mode?"
ANSWER = ("It does sound worse. It usually isn't. The interesting question is almost never "
          "is this certificate valid. It's which service is this, and is it allowed to call me? "
          "Passthrough hands you the whole chain and lets your own code answer that.")
EXPERT_ASKS = ("So walk me through it. If the load balancer isn't validating anything, "
               "what am I actually getting out of passthrough?")

SAMPLES = {
    "m1-stephen": [("stephen", SHAPED), ("tiffany", ANSWER)],
    "m2-matthew": [("matthew", SHAPED), ("tiffany", ANSWER)],
    "m3-brian": [("brian", SHAPED), ("tiffany", ANSWER)],
    "m4-gregory": [("gregory", SHAPED), ("tiffany", ANSWER)],
    "m5-patrick": [("patrick", SHAPED), ("tiffany", ANSWER)],
    "t1-terse": [("stephen", TERSE)],
    "t2-shaped": [("stephen", SHAPED)],
    "r1-swap": [("tiffany", EXPERT_ASKS), ("brian", ANSWER)],
}

GAP_MS = 260
polly = boto3.Session(region_name=REGION).client("polly")


def source(key, text):
    spec = VOICES[key]
    name = "%s-%s.ogg" % (spec["voice"], hashlib.md5(text.encode()).hexdigest()[:8])
    path = SRC / name
    if not path.exists():
        response = polly.synthesize_speech(
            Text="<speak>%s</speak>" % text,
            TextType="ssml",
            OutputFormat="ogg_vorbis",
            VoiceId=spec["voice"],
            Engine=spec["engine"],
            SampleRate="24000",
        )
        path.write_bytes(response["AudioStream"].read())
        print("  polly %s" % name)
    return path


def prepare(clip, wav, chain):
    edge = "silenceremove=start_periods=1:start_threshold=-60dB:start_silence=0.15"
    steps = [edge, "areverse", edge, "areverse", "afade=t=in:d=0.008"] + chain
    steps.append("apad=pad_dur=%s" % (GAP_MS / 1000))
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(clip), "-af", ",".join(steps),
         "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le", str(wav)],
        check=True,
    )


def graph(wavs, tail):
    return "".join("[%d:a]" % i for i in range(len(wavs))) + \
        "concat=n=%d:v=0:a=1[cat];[cat]%s[out]" % (len(wavs), tail)


def measure(wavs):
    tail = "loudnorm=I=%s:TP=-1.5:print_format=json" % TARGET_LUFS
    command = ["ffmpeg", "-hide_banner"]
    for wav in wavs:
        command += ["-i", str(wav)]
    command += ["-filter_complex", graph(wavs, tail), "-map", "[out]", "-f", "null", "-"]
    err = subprocess.run(command, capture_output=True, text=True).stderr
    return json.loads(err[err.rindex("{"):err.rindex("}") + 1])


def build(name):
    turns = SAMPLES[name]
    work = OUT / name
    work.mkdir(parents=True, exist_ok=True)
    print(name)

    wavs = []
    for index, (key, text) in enumerate(turns, 1):
        clip = source(key, text)
        wav = work / ("%d-%s.wav" % (index, VOICES[key]["voice"]))
        prepare(clip, wav, VOICES[key]["chain"])
        wavs.append(wav)

    gain = TARGET_LUFS - float(measure(wavs)["input_i"])
    tail = "volume=%.2fdB,alimiter=limit=0.89:level=false,aresample=24000" % gain
    episode = OUT / (name + ".mp3")
    command = ["ffmpeg", "-y", "-v", "error"]
    for wav in wavs:
        command += ["-i", str(wav)]
    command += ["-filter_complex", graph(wavs, tail), "-map", "[out]",
                "-ar", "24000", "-ac", "1", "-c:a", "libmp3lame", "-b:a", "192k", str(episode)]
    subprocess.run(command, check=True, capture_output=True)
    print("  gain %+.2f dB -> %s" % (gain, episode.relative_to(ROOT)))


SRC.mkdir(parents=True, exist_ok=True)
for name in sys.argv[1:] or list(SAMPLES):
    build(name)
