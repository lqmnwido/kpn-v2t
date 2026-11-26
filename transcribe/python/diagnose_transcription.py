import os
import sys
import torch
import numpy as np
import json
from faster_whisper import WhisperModel

def diagnose():
    print("=== System Information ===")
    print(f"Python Version: {sys.version}")
    print(f"PyTorch Version: {torch.__version__}")
    print(f"CUDA Available: {torch.cuda.is_available()}")
    
    device = "cpu"
    # Testing both int8 and float32 as int8 can fail on some CPUs
    for compute_type in ["int8", "float32"]:
        print(f"\n--- Testing compute_type: {compute_type} ---")
        try:
            model_name = "tiny" # Smallest for testing
            print(f"Loading model '{model_name}' on {device}...")
            model = WhisperModel(model_name, device=device, compute_type=compute_type)
            print("Model loaded successfully.")
            
            # Create a 1-second dummy audio (silence)
            audio = np.zeros(16000, dtype=np.float32)
            
            print("Starting transcription test with VAD...")
            segments, info = model.transcribe(
                audio,
                beam_size=1,
                vad_filter=True,
                vad_parameters=dict(min_silence_duration_ms=500)
            )
            
            print(f"Transcribe returned segments type: {type(segments)}")
            if segments is None:
                print("ERROR: transcribe returned None instead of a generator!")
            else:
                # This is where 'NoneType' object is not iterable usually happens
                results = list(segments)
                print(f"Transcription successful. Results: {results}")
                
        except Exception as e:
            print(f"Caught Exception: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    diagnose()
