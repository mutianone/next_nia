"""Bounded local conversion; original recordings are never modified."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

MAX_OUTPUT_BYTES = 20 * 1024 * 1024
MAX_SECONDS = 3600
TIMEOUT_SECONDS = 90
DEMUXERS = {"wav": "wav", "webm": "matroska", "ogg": "ogg", "m4a": "mov", "flac": "flac"}


class RecordMediaError(Exception):
    pass


def conversion_binary() -> str | None:
    configured = os.environ.get("CRJ_FFMPEG_BIN", "ffmpeg")
    binary = shutil.which(configured)
    return str(Path(binary).resolve()) if binary else None


def conversion_available() -> bool:
    # Presence is not a claim that every codec or submitted file is decodable.
    return conversion_binary() is not None


def normalize_for_asr(raw: bytes, file_format: str) -> tuple[bytes, str, str]:
    if not raw or len(raw) > MAX_OUTPUT_BYTES:
        raise RecordMediaError("音频为空或超过 20 MiB，请分段后重试；原件保持不变。")
    if file_format == "mp3":
        return raw, "mp3", "audio/mpeg"
    demuxer = DEMUXERS.get(file_format)
    if not demuxer:
        raise RecordMediaError("此录音格式不能用于转写；原件保持不变。")
    binary = conversion_binary()
    if not binary:
        raise RecordMediaError("此音频需本地 FFmpeg 转为 MP3 后转写。请在服务器安装 FFmpeg 或上传 MP3；原件已保留，尚未发送。")
    try:
        # Temp directories are private; names, metadata and filesystem paths
        # never become API inputs. Explicit demuxers and protocols prevent URLs
        # or playlists from being resolved by FFmpeg.
        with tempfile.TemporaryDirectory(prefix="crj-audio-") as directory:
            source, output = Path(directory) / "input", Path(directory) / "output.mp3"
            descriptor = os.open(source, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(raw)
            # Fail on decoding errors instead of returning a partially decoded
            # recording. Map every audio stream: the MP3 muxer requires exactly
            # one, so multi-track input is rejected rather than silently losing
            # all but its first track. This needs no separate probing tool.
            command = [binary, "-nostdin", "-hide_banner", "-loglevel", "error", "-xerror",
                       "-protocol_whitelist", "file,pipe", "-err_detect", "explode", "-f", demuxer, "-i", str(source),
                       "-map", "0:a", "-vn", "-sn", "-dn", "-map_metadata", "-1", "-map_chapters", "-1",
                       "-af", "asetpts=PTS-STARTPTS", "-ac", "1", "-ar", "16000",
                       "-c:a", "libmp3lame", "-b:a", "48k", "-f", "mp3", "-t", str(MAX_SECONDS + 1),
                       "-fs", str(MAX_OUTPUT_BYTES), "-progress", "pipe:1", "-y", str(output)]
            result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    timeout=TIMEOUT_SECONDS, check=False)
            if result.returncode != 0 or not output.is_file():
                raise RecordMediaError("本地音频转换失败，请确认文件完整、仅含一条有效音轨且 FFmpeg 支持其编码；多音轨文件请先选取所需音轨导出。原件未修改，尚未发送。")
            elapsed = [int(line.split(b"=", 1)[1]) for line in result.stdout.splitlines()
                       if line.startswith(b"out_time_us=") and line.split(b"=", 1)[1].isdigit()]
            if not elapsed:
                raise RecordMediaError("无法核对转换结果的时长，本次未发送，请更换音频后重试。")
            if max(elapsed) >= MAX_SECONDS * 1_000_000:
                raise RecordMediaError("音频达到一小时时长上限，请拆分为较短片段；未发送截断的音频。")
            # FFmpeg's byte cap may stop between frames. Reject the entire result
            # near the cap instead of accepting a potentially truncated file.
            size = output.stat().st_size
            if size >= MAX_OUTPUT_BYTES - 65536:
                raise RecordMediaError("转换后的音频接近 20 MiB 上限，请拆分片段；未发送可能截断的音频。")
            if size < 128:
                raise RecordMediaError("转换后的音频为空或无有效内容，请检查原件后重试。")
            return output.read_bytes(), "mp3", "audio/mpeg"
    except RecordMediaError:
        raise
    except subprocess.TimeoutExpired:
        raise RecordMediaError("本地音频转换超时，请拆分或重新导出音频；原件未修改，尚未发送。") from None
    except (OSError, ValueError):
        raise RecordMediaError("本地音频转换无法完成，请检查 FFmpeg、临时目录权限与磁盘空间；尚未发送。") from None


def extract_speech_window(raw: bytes, file_format: str, start_ms: int, end_ms: int) -> bytes:
    """Decode a bounded original-audio window; never overwrite the source file."""
    if not raw or len(raw)>MAX_OUTPUT_BYTES or not 0<=start_ms<end_ms or end_ms-start_ms>60000:
        raise RecordMediaError('补转写片段必须在60秒以内。')
    demuxer='mp3' if file_format=='mp3' else DEMUXERS.get(file_format)
    binary=conversion_binary()
    if not binary or not demuxer:raise RecordMediaError('无法解码此原音，请检查FFmpeg和音频格式。')
    try:
        with tempfile.TemporaryDirectory(prefix='crj-repair-') as directory:
            source=Path(directory)/'source';source.write_bytes(raw);source.chmod(0o600)
            command=[binary,'-nostdin','-v','error','-xerror','-protocol_whitelist','file,pipe','-f',demuxer,'-i',str(source),
                '-ss',str(start_ms/1000),'-t',str((end_ms-start_ms)/1000),'-map','0:a','-vn','-sn','-dn',
                '-ac','1','-ar','16000','-c:a','pcm_s16le','-f','s16le','pipe:1']
            result=subprocess.run(command,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=30,check=False)
            if result.returncode or len(result.stdout)<128:raise RecordMediaError('原音片段解码失败，原文未修改。')
            import io,wave
            buffer=io.BytesIO()
            with wave.open(buffer,'wb') as target:
                target.setnchannels(1);target.setsampwidth(2);target.setframerate(16000);target.writeframes(result.stdout)
            return buffer.getvalue()
    except (OSError,subprocess.TimeoutExpired):raise RecordMediaError('原音片段处理失败，原文未修改。') from None
