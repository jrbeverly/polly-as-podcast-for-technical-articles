import json
import re
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape

import boto3

REGION = "us-east-1"
ROOT = Path(__file__).parent
BUILD = ROOT / "build"

# Only terms Polly gets wrong on its own. AWS, S3, SAN, TLS, ALB, ACM, SNI,
# CloudWatch, execute-api and x509 already come back correct from verify.py, and
# a <sub> around a short acronym costs a phrase break, so they are not here.
PRONUNCIATION = [
    (r"\bmTLS\b", '<sub alias="mutual T L S">mTLS</sub>'),
    (r"\bSPIFFE\b", '<sub alias="spiffy">SPIFFE</sub>'),
    (r"\b403\b", '<sub alias="four oh three">403</sub>'),
]

VARIANTS = {
    "v1-neural-baseline": {
        "engine": "neural",
        "voices": {"host": "Matthew", "expert": "Joanna"},
        "rate": None,
        "domain": None,
        "pronounce": False,
        "sentence_break_ms": 300,
        "gap_ms": 400,
        "question_gap_ms": 400,
        "trim_silence": False,
        "loudness": None,
    },
    "v2-generative": {
        "engine": "generative",
        "voices": {"host": "Stephen", "expert": "Ruth"},
        "rate": None,
        "domain": None,
        "pronounce": True,
        "sentence_break_ms": 0,
        "gap_ms": 320,
        "question_gap_ms": 200,
        "trim_silence": True,
        "loudness": -16.0,
    },
    "v3-longform": {
        "engine": "long-form",
        "voices": {"host": "Gregory", "expert": "Danielle"},
        "rate": "96%",
        "domain": None,
        "pronounce": True,
        "sentence_break_ms": 120,
        "gap_ms": 360,
        "question_gap_ms": 240,
        "trim_silence": True,
        "loudness": -16.0,
    },
    "v4-generative-tight": {
        "engine": "generative",
        "voices": {"host": "Matthew", "expert": "Danielle"},
        "rate": None,
        "domain": None,
        "pronounce": True,
        "sentence_break_ms": 0,
        "gap_ms": 190,
        "question_gap_ms": 130,
        "trim_silence": True,
        "loudness": -16.0,
    },
    "v5-newscaster": {
        "engine": "neural",
        "voices": {"host": "Matthew", "expert": "Joanna"},
        "rate": None,
        "domain": "news",
        "pronounce": True,
        "sentence_break_ms": 0,
        "gap_ms": 300,
        "question_gap_ms": 220,
        "trim_silence": True,
        "loudness": -16.0,
    },
}

LEAD_IN_MS = 300
TAIL_MS = 700
SAMPLE_RATE = "24000"

polly = boto3.Session(region_name=REGION).client("polly")


def to_ssml(text, variant):
    body = escape(text)
    if variant["pronounce"]:
        for pattern, replacement in PRONUNCIATION:
            body = re.sub(pattern, replacement, body)
    if variant["sentence_break_ms"]:
        gap = variant["sentence_break_ms"]
        body = re.sub(r"([.!?])(\s+|$)", r'\1<break time="%dms"/>\2' % gap, body)
    if variant["rate"]:
        body = '<prosody rate="%s">%s</prosody>' % (variant["rate"], body)
    if variant["domain"]:
        body = '<amazon:domain name="%s">%s</amazon:domain>' % (variant["domain"], body)
    return "<speak>%s</speak>" % body


def synthesize(ssml, voice, engine, mp3):
    key = json.dumps([voice, engine, ssml])
    key_file = mp3.with_suffix(".key")
    if mp3.exists() and key_file.exists() and key_file.read_text() == key:
        return False
    response = polly.synthesize_speech(
        Text=ssml,
        TextType="ssml",
        OutputFormat="mp3",
        VoiceId=voice,
        Engine=engine,
        SampleRate=SAMPLE_RATE,
    )
    mp3.write_bytes(response["AudioStream"].read())
    key_file.write_text(key)
    return True


def gap_after(turns, index, variant):
    turn = turns[index]
    if index == len(turns) - 1:
        return TAIL_MS
    if "gap_after_ms" in turn:
        return turn["gap_after_ms"]
    if turn["text"].rstrip().endswith("?"):
        return variant["question_gap_ms"]
    return variant["gap_ms"]


def prepare_clip(mp3, wav, gap_ms, lead_ms, trim):
    chain = []
    if trim:
        edge = "silenceremove=start_periods=1:start_threshold=-50dB:start_silence=0.05"
        chain += [edge, "areverse", edge, "areverse"]
    if lead_ms:
        chain.append("adelay=%d" % lead_ms)
    chain.append("apad=pad_dur=%s" % (gap_ms / 1000))
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(mp3), "-af", ",".join(chain),
         "-ar", SAMPLE_RATE, "-ac", "1", "-c:a", "pcm_s16le", str(wav)],
        check=True, capture_output=True,
    )


def duration(path):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        check=True, capture_output=True, text=True,
    )
    return float(result.stdout.strip())


def measure_loudness(wavs, target):
    graph = concat_graph(wavs) + (
        ";[cat]loudnorm=I=%s:TP=-1.5:print_format=json[out]" % target
    )
    command = ["ffmpeg", "-hide_banner"]
    for wav in wavs:
        command += ["-i", str(wav)]
    command += ["-filter_complex", graph, "-map", "[out]", "-f", "null", "-"]
    stderr = subprocess.run(command, check=True, capture_output=True, text=True).stderr
    return json.loads(stderr[stderr.rindex("{"):stderr.rindex("}") + 1])


def concat_graph(wavs):
    return "".join("[%d:a]" % i for i in range(len(wavs))) + \
        "concat=n=%d:v=0:a=1[cat]" % len(wavs)


def assemble(wavs, episode, target):
    graph = concat_graph(wavs)
    if target is None:
        graph += ";[cat]anull[out]"
    else:
        gain = target - float(measure_loudness(wavs, target)["input_i"])
        graph += ";[cat]volume=%.2fdB,alimiter=limit=0.63:level=false[out]" % gain
    command = ["ffmpeg", "-y", "-hide_banner"]
    for wav in wavs:
        command += ["-i", str(wav)]
    command += ["-filter_complex", graph, "-map", "[out]",
                "-ar", SAMPLE_RATE, "-ac", "1", "-c:a", "libmp3lame", "-b:a", "64k",
                str(episode)]
    subprocess.run(command, check=True, capture_output=True)


def render(name, script):
    variant = VARIANTS[name]
    out = BUILD / name
    for sub in ("turns", "clips", "prepared"):
        (out / sub).mkdir(parents=True, exist_ok=True)

    turns = script["turns"]
    wavs = []
    entries = []
    start = 0.0
    for index, turn in enumerate(turns):
        stem = "%03d-%s" % (index + 1, turn["speaker"])
        ssml = to_ssml(turn["text"], variant)
        (out / "turns" / (stem + ".ssml.xml")).write_text(ssml)

        mp3 = out / "clips" / (stem + ".mp3")
        fresh = synthesize(ssml, variant["voices"][turn["speaker"]], variant["engine"], mp3)

        wav = out / "prepared" / (stem + ".wav")
        gap = gap_after(turns, index, variant)
        prepare_clip(mp3, wav, gap, LEAD_IN_MS if index == 0 else 0, variant["trim_silence"])
        wavs.append(wav)

        length = duration(wav)
        entries.append({
            "index": index + 1,
            "speaker": turn["speaker"],
            "voice": variant["voices"][turn["speaker"]],
            "clip": mp3.name,
            "gap_after_ms": gap,
            "start_seconds": round(start, 3),
            "seconds": round(length, 3),
            "synthesized": fresh,
        })
        start += length
        print("  %s %-6s %5.2fs %s" % (stem, variant["voices"][turn["speaker"]], length,
                                       "polly" if fresh else "cached"))

    episode = out / "episode.mp3"
    assemble(wavs, episode, variant["loudness"])
    total = duration(episode)
    (out / "timeline.json").write_text(json.dumps({
        "variant": name,
        "settings": variant,
        "total_seconds": round(total, 3),
        "turns": entries,
    }, indent=2))
    print("  -> %s  %.2fs" % (episode.relative_to(ROOT), total))
    return total


def write_compare(names):
    rows = []
    for name in names:
        timeline = json.loads((BUILD / name / "timeline.json").read_text())
        settings = timeline["settings"]
        clips = "".join(
            '<tr><td>%d</td><td>%s / %s</td><td>%.2fs</td>'
            '<td><audio controls preload="none" src="%s/clips/%s"></audio></td>'
            '<td><code>%s</code></td></tr>' % (
                turn["index"], turn["speaker"], turn["voice"], turn["seconds"],
                name, turn["clip"],
                escape((BUILD / name / "turns" / turn["clip"].replace(".mp3", ".ssml.xml")).read_text()))
            for turn in timeline["turns"])
        rows.append(
            '<section><h2>%s</h2>'
            '<p>%s &middot; %s &middot; gaps %d/%d ms &middot; %.1fs</p>'
            '<audio controls preload="none" src="%s/episode.mp3"></audio>'
            '<details><summary>turns</summary><table>%s</table></details></section>' % (
                name, settings["engine"],
                " + ".join(sorted(set(settings["voices"].values()))),
                settings["gap_ms"], settings["question_gap_ms"],
                timeline["total_seconds"], name, clips))
    (BUILD / "compare.html").write_text(
        "<!doctype html><meta charset=utf-8><title>Polly podcast variants</title>"
        "<style>body{font:15px system-ui;max-width:60rem;margin:2rem auto;padding:0 1rem}"
        "section{border-top:1px solid #ccc;padding:1rem 0}audio{width:100%;max-width:34rem}"
        "table{width:100%;border-collapse:collapse}td{padding:.3rem;vertical-align:top;"
        "border-bottom:1px solid #eee}code{font-size:12px;white-space:pre-wrap}</style>"
        "<h1>Polly podcast variants</h1>" + "".join(rows))
    print("-> %s" % (BUILD / "compare.html").relative_to(ROOT))


script = json.loads((ROOT / "script.json").read_text())
for name in sys.argv[1:] or list(VARIANTS):
    print(name)
    render(name, script)
write_compare([n for n in VARIANTS if (BUILD / n / "timeline.json").exists()])
