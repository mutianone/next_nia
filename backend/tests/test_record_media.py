from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
import subprocess
import wave

import pytest

from backend.app import record_media as media


def test_mp3_is_not_reencoded_or_sent_anywhere(monkeypatch):
    monkeypatch.setattr(media.subprocess, "run", lambda *a, **k: pytest.fail("MP3 must remain original"))
    data = b"ID3" + b"test" * 100
    assert media.normalize_for_asr(data, "mp3") == (data, "mp3", "audio/mpeg")


def test_missing_local_converter_is_actionable_and_no_network(monkeypatch):
    monkeypatch.setattr(media, "conversion_binary", lambda: None)
    with pytest.raises(media.RecordMediaError, match="安装 FFmpeg 或上传 MP3"):
        media.normalize_for_asr(b"RIFFtest", "wav")


def mock_conversion(monkeypatch, *, elapsed=2_000_000, size=1024, code=0):
    paths = []
    monkeypatch.setattr(media, "conversion_binary", lambda: "/local/ffmpeg")
    def run(command, **kwargs):
        source = Path(command[command.index("-i") + 1])
        output = Path(command[-1])
        paths.extend([source, output])
        assert source.read_bytes() == b"RIFFtest"
        assert source.stat().st_mode & 0o777 == 0o600
        assert command[command.index("-protocol_whitelist") + 1] == "file,pipe"
        assert "-xerror" in command
        assert command[command.index("-err_detect") + 1] == "explode"
        assert command[command.index("-map") + 1] == "0:a"
        assert command[command.index("-c:a") + 1] == "libmp3lame"
        assert command[command.index("-b:a") + 2:command.index("-b:a") + 4] == ["-f", "mp3"]
        assert command[command.index("-map_metadata") + 1] == "-1"
        assert kwargs["timeout"] == 90
        assert kwargs["stderr"] == subprocess.DEVNULL
        with output.open("wb") as stream:
            stream.truncate(size)
        return SimpleNamespace(returncode=code, stdout=f"out_time_us={elapsed}\nprogress=end\n".encode())
    monkeypatch.setattr(media.subprocess, "run", run)
    return paths


def test_conversion_is_private_bounded_and_removes_temporary_files(monkeypatch):
    paths = mock_conversion(monkeypatch)
    content, file_format, mime_type = media.normalize_for_asr(b"RIFFtest", "wav")
    assert len(content) == 1024
    assert (file_format, mime_type) == ("mp3", "audio/mpeg")
    assert all(not path.exists() for path in paths)


@pytest.mark.parametrize("options,message", [
    ({"elapsed": 3601_000_000}, "一小时"),
    ({"size": media.MAX_OUTPUT_BYTES - 100}, "截断"),
    ({"size": 0}, "为空"),
    ({"code": 1}, "转换失败"),
])
def test_invalid_or_truncated_conversion_is_never_returned(monkeypatch, options, message):
    paths = mock_conversion(monkeypatch, **options)
    with pytest.raises(media.RecordMediaError, match=message):
        media.normalize_for_asr(b"RIFFtest", "wav")
    assert all(not path.exists() for path in paths)


def test_timeout_and_local_errors_do_not_expose_paths(monkeypatch):
    monkeypatch.setattr(media, "conversion_binary", lambda: "/secret/path/ffmpeg")
    def run(*args, **kwargs):
        raise subprocess.TimeoutExpired("secret-path", 90)
    monkeypatch.setattr(media.subprocess, "run", run)
    with pytest.raises(media.RecordMediaError, match="超时") as error:
        media.normalize_for_asr(b"RIFFtest", "wav")
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("data,kind", [(b"", "wav"), (b"a", "hls"), (b"x" * (media.MAX_OUTPUT_BYTES + 1), "mp3")])
def test_empty_oversized_or_unsupported_input_is_rejected(data, kind):
    with pytest.raises(media.RecordMediaError):
        media.normalize_for_asr(data, kind)


@pytest.mark.skipif(not media.conversion_available(), reason="Local FFmpeg is optional")
def test_installed_ffmpeg_converts_synthetic_wav_locally():
    audio = BytesIO()
    with wave.open(audio, "wb") as file:
        file.setnchannels(1)
        file.setsampwidth(2)
        file.setframerate(16000)
        file.writeframes(b"\0\0" * 16000)
    original = audio.getvalue()
    converted, kind, mime_type = media.normalize_for_asr(original, "wav")
    assert original == audio.getvalue()
    assert len(converted) > 128
    assert (kind, mime_type) == ("mp3", "audio/mpeg")


def generate_audio(path, *arguments):
    """Generate test tones locally; never record microphone or contact a provider."""
    subprocess.run([media.conversion_binary(), "-nostdin", "-hide_banner", "-loglevel", "error",
                    *arguments, "-y", str(path)],
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30, check=True)
    return path.read_bytes()


@pytest.mark.skipif(not media.conversion_available(), reason="Local FFmpeg is optional")
def test_installed_ffmpeg_rejects_partially_decodable_flac(tmp_path):
    original = generate_audio(tmp_path / "tone.flac", "-f", "lavfi", "-i",
                              "sine=frequency=440:duration=8:sample_rate=16000", "-c:a", "flac")
    assert len(media.normalize_for_asr(original, "flac")[0]) > 128
    # Some frames remain decodable. Default FFmpeg can still exit successfully
    # after reporting a decode error, which previously returned partial audio.
    damaged = original[:len(original) * 2 // 3]
    with pytest.raises(media.RecordMediaError, match="转换失败") as error:
        media.normalize_for_asr(damaged, "flac")
    assert str(tmp_path) not in str(error.value)
    assert "原件未修改，尚未发送" in str(error.value)


@pytest.mark.skipif(not media.conversion_available(), reason="Local FFmpeg is optional")
def test_installed_ffmpeg_rejects_multiple_m4a_audio_tracks(tmp_path):
    original = generate_audio(tmp_path / "two-tracks.m4a",
                              "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                              "-f", "lavfi", "-i", "sine=frequency=880:duration=2",
                              "-map", "0:a", "-map", "1:a", "-c:a", "aac")
    selected = generate_audio(tmp_path / "selected-track.m4a", "-i", str(tmp_path / "two-tracks.m4a"),
                              "-map", "0:a:0", "-c:a", "copy")
    assert len(media.normalize_for_asr(selected, "m4a")[0]) > 128
    # A track is valid when explicitly extracted. Selecting only 0:a:0 used to silently
    # return a one-second recording and omit the second two-second track.
    with pytest.raises(media.RecordMediaError, match="多音轨") as error:
        media.normalize_for_asr(original, "m4a")
    assert str(tmp_path) not in str(error.value)
    assert "原件未修改，尚未发送" in str(error.value)


@pytest.mark.skipif(not media.conversion_available(), reason="Local FFmpeg is optional")
def test_packaged_healthcheck_contains_decodable_audio(tmp_path):
    asset=Path(__file__).resolve().parents[1]/'assets'/'healthcheck.mp3'
    decoded=generate_audio(tmp_path/'healthcheck.wav','-i',str(asset),'-ar','16000','-ac','1','-c:a','pcm_s16le')
    with wave.open(BytesIO(decoded)) as audio:
        assert 2 < audio.getnframes()/audio.getframerate() < 15
        assert any(audio.readframes(audio.getnframes()))
