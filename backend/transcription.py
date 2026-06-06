import os
import subprocess
import tempfile

# We will lazily load whisper to avoid import overhead if it isn't installed yet
whisper_model = None

def get_whisper_model():
    global whisper_model
    if whisper_model is None:
        try:
            import whisper
            # Using 'tiny' or 'base' for zero-cost, local speed on CPU
            whisper_model = whisper.load_model("tiny")
        except ImportError:
            raise ImportError("openai-whisper library is not installed. Run pip install openai-whisper.")
    return whisper_model

def extract_audio_from_video(video_path, output_audio_path):
    """Extract mono 16kHz audio from video using local ffmpeg."""
    try:
        command = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-vn",
            "-acodec", "pcm_s16le",
            "-ar", "16000",
            "-ac", "1",
            output_audio_path
        ]
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        return True
    except subprocess.CalledProcessError as e:
        print(f"FFmpeg error: {e.stderr.decode('utf-8', errors='ignore')}")
        return False
    except Exception as e:
        print(f"Failed to run FFmpeg: {e}")
        return False

def transcribe_video(video_path, source_name=None):
    chunks = []
    filename = source_name or os.path.basename(video_path)
    
    # Create temp wav file
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_audio:
        temp_audio_path = temp_audio.name
        
    try:
        print(f"Extracting audio from {filename}...")
        success = extract_audio_from_video(video_path, temp_audio_path)
        if not success:
            return []
            
        print(f"Transcribing audio from {filename} locally via Whisper...")
        model = get_whisper_model()
        result = model.transcribe(temp_audio_path, beam_size=1)
        
        # Whisper segments contain start, end timestamps and text
        for segment in result.get("segments", []):
            start = segment.get("start", 0)
            end = segment.get("end", 0)
            text = segment.get("text", "").strip()
            
            # Format timestamp as MM:SS
            def format_time(seconds):
                mins = int(seconds // 60)
                secs = int(seconds % 60)
                return f"{mins:02d}:{secs:02d}"
                
            if text:
                chunks.append({
                    "source": filename,
                    "type": "video",
                    "location": f"Timestamp {format_time(start)} - {format_time(end)}",
                    "content": text
                })
    except Exception as e:
        print(f"Error transcribing video {video_path}: {e}")
    finally:
        if os.path.exists(temp_audio_path):
            os.remove(temp_audio_path)
            
    return chunks
