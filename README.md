# Polly as Podcast for Technical Articles

> [!WARNING]
> **AI-authored:** This change was autonomously planned and implemented by an AI software factory from a human-authored specification, with possible subsequent human review or modification.

Renders a finished two-speaker technical script (`script.json`) into a mastered podcast with Amazon Polly, one clip per speaker turn, in named variants so engines, voices and pacing can be compared by ear. Follows the per-turn model of Repodcast.

Needs Python 3 with `boto3`, FFmpeg, and AWS credentials that can call Polly in `us-east-1`.

```sh
python3 podcast.py
python3 podcast.py v2-generative
open build/compare.html

python3 profiles.py
ls build/profiles/*.mp3

python3 demo.py
```

Clips are cached against their SSML, so editing one line of `script.json` re-synthesizes only that turn.

`verify.py` round-trips each episode through Amazon Transcribe to check that acronyms land. It needs the S3 bucket in `terraform/` (`terraform apply`, then `terraform destroy`). Nothing else creates AWS resources.

## Notes

- Results do not yield desired direction. Need to identify other approaches.
