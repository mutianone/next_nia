"""Recompute the frozen 2026-11-18 comparison; no network or model calls."""
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / "docs/asr-model-evidence-20261118"


def read(name):
    return json.loads((ROOT / name).read_text())


def normalize(text):
    return "".join(char.lower() for char in text if char.isalnum())


def distance(left, right):
    previous = list(range(len(right) + 1))
    for i, a in enumerate(left, 1):
        current = [i]
        for j, b in enumerate(right, 1):
            current.append(min(current[-1] + 1, previous[j] + 1,
                               previous[j - 1] + (a != b)))
        previous = current
    return previous[-1]


def main():
    manifest = {item["id"]: item for item in read("manifest.json")}
    summary = read("summary.json")
    assert set(manifest) == {f"{i:02}" for i in range(1, 13)}
    assert abs(sum(s["end"] - s["start"] for s in manifest.values()) - 249.642) < .001
    files = ["sensevoice.json", "qwen3-asr.json", "xingchen.json", "xingchen-original-wav.json"]
    expected_metrics = summary["models"] + [summary["original_wav"]]
    asr_count = template_count = 0
    for filename, expected in zip(files, expected_metrics):
        group = read(filename)
        cases = group["cases"]
        assert len(cases) == 12 and {c["id"] for c in cases} == set(manifest)
        metrics = dict(model=expected["model"], edits=0, characters=0, stable=0, exact=0, short=0)
        actual = not filename.endswith("original-wav.json")
        if actual:
            metrics.update(saved=0, faithful=0, complete_asr=0, blank=0, failed=0, supplements=0)
        for case in cases:
            identifier, score, asr = case["id"], case["score"], case["asr"]
            source = manifest[identifier]
            assert asr["sha256"] == source["sha256"], (filename, identifier, "source hash")
            assert asr["model"] == expected["model"]
            assert asr["provider"] == "siliconflow"
            segments = asr["segments"]
            reference = "".join(t["text"] for t in source["refs"])
            expected_answer = "".join(t["text"] for t in source["refs"] if t["role"] == "答")
            cut = score["cut"]  # Frozen manual annotation; not inferred from alternating turns.
            assert cut is None or 0 <= cut <= len(segments)
            answer = "\n".join(s["text"] for s in segments[cut:]) if cut is not None else None
            question_ids = {s.get("speaker_id") for s in segments[:cut]} if cut is not None else set()
            answer_ids = {s.get("speaker_id") for s in segments[cut:]} if cut is not None else set()
            stable = (cut is not None and len(question_ids) == len(answer_ids) == 1
                      and None not in question_ids | answer_ids and question_ids != answer_ids)
            exact = answer is not None and normalize(answer) == normalize(expected_answer)
            edits = distance(normalize(reference), normalize(asr["text"]))
            characters = len(normalize(reference))
            assert score["reference_answer"] == expected_answer
            assert score["transcribed_answer"] == answer
            assert (score["stable"], score["exact_answer"], score["character_edits"], score["characters"]) == (
                stable, exact, edits, characters), (filename, identifier, "score")
            metrics["edits"] += edits
            metrics["characters"] += characters
            metrics["stable"] += stable
            metrics["exact"] += exact
            metrics["short"] += identifier in summary["short_cases"] and exact
            asr_count += 1
            call = case["actual_template"]
            if not actual:
                assert call is None
                continue
            assert call is not None
            template_count += 1
            if call["status"] != 200:
                metrics["failed"] += 1
                assert call.get("error") and call.get("value") is None
                continue
            value = call.get("value")
            if value is None:
                metrics["blank"] += 1
                continue
            assert value in call["candidate_values"] and value in call["rendered_text"]
            assert any(f["value"] == value and f.get("evidence") for f in call["facts"])
            metrics["saved"] += 1
            metrics["faithful"] += normalize(value) == normalize(expected_answer)
            metrics["complete_asr"] += value == answer
            metrics["supplements"] += identifier in summary["supplement_cases"] and value == answer
        assert metrics == expected, (filename, metrics, expected)
        print(f"{filename}: CER {metrics['edits']}/{metrics['characters']}; all metrics match")
    print(f"Verified {asr_count} ASR results and {template_count} actual template workflow records.")


if __name__ == "__main__":
    main()
