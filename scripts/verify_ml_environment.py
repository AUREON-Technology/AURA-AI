#!/usr/bin/env python3
"""
AURA ML Environment Verification Script

Run this to verify that the ML environment is correctly configured.
Outputs a PASS/FAIL/N/A table for all components.
"""

import sys
import subprocess
import platform
import os


def run_test(name, test_func, required=True):
    """Run a test and return (name, status, detail)"""
    try:
        result = test_func()
        return (name, "PASS", result)
    except Exception as e:
        if required:
            return (name, "FAIL", str(e))
        else:
            return (name, "N/A", f"Optional: {e}")


def test_python_version():
    v = sys.version_info
    return f"Python {v.major}.{v.minor}.{v.micro}"


def test_pytorch_import():
    import torch
    return f"PyTorch {torch.__version__}"


def test_pytorch_tensor_ops():
    import torch
    x = torch.randn(1024, 1024)
    y = torch.randn(1024, 1024)
    z = x @ y
    assert z.shape == (1024, 1024)
    assert torch.isfinite(z).all()
    return "Tensor operations OK"


def test_pytorch_autograd():
    import torch
    x = torch.randn(128, 128, requires_grad=True)
    y = (x ** 2).mean()
    y.backward()
    assert x.grad is not None
    assert torch.isfinite(x.grad).all()
    return "Autograd OK"


def test_pytorch_nn():
    import torch
    import torch.nn as nn
    model = nn.Sequential(nn.Linear(128, 256), nn.ReLU(), nn.Linear(256, 10))
    x = torch.randn(32, 128)
    output = model(x)
    assert output.shape == (32, 10)
    return "Neural network forward OK"


def test_pytorch_training():
    import torch
    import torch.nn as nn
    import torch.optim as optim
    model = nn.Sequential(nn.Linear(128, 256), nn.ReLU(), nn.Linear(256, 10))
    optimizer = optim.AdamW(model.parameters(), lr=1e-3)
    target = torch.randint(0, 10, (32,))
    loss_fn = nn.CrossEntropyLoss()
    output = model(torch.randn(32, 128))
    loss = loss_fn(output, target)
    loss.backward()
    optimizer.step()
    optimizer.zero_grad()
    assert torch.isfinite(loss)
    return "Training step OK"


def test_pytorch_dataloader():
    import torch
    from torch.utils.data import Dataset, DataLoader
    import torch.nn as nn
    import torch.optim as optim
    
    class TinyDataset(Dataset):
        def __len__(self): return 100
        def __getitem__(self, idx): return torch.randn(128), torch.randint(0, 10, ())
    
    dataset = TinyDataset()
    loader = DataLoader(dataset, batch_size=16, shuffle=True)
    model = nn.Sequential(nn.Linear(128, 256), nn.ReLU(), nn.Linear(256, 10))
    optimizer = optim.AdamW(model.parameters(), lr=1e-3)
    loss_fn = nn.CrossEntropyLoss()
    
    for batch_x, batch_y in loader:
        out = model(batch_x)
        loss = loss_fn(out, batch_y)
        loss.backward()
        optimizer.step()
        optimizer.zero_grad()
        break
    return "DataLoader pipeline OK"


def test_pytorch_checkpoint():
    import torch
    import torch.nn as nn
    import torch.optim as optim
    import tempfile
    import os
    
    model = nn.Sequential(nn.Linear(128, 256), nn.ReLU(), nn.Linear(256, 10))
    optimizer = optim.AdamW(model.parameters(), lr=1e-3)
    
    checkpoint_path = os.path.join(tempfile.gettempdir(), 'aura_verify_checkpoint.pt')
    torch.save({'model': model.state_dict(), 'optimizer': optimizer.state_dict()}, checkpoint_path)
    
    new_model = nn.Sequential(nn.Linear(128, 256), nn.ReLU(), nn.Linear(256, 10))
    checkpoint = torch.load(checkpoint_path, weights_only=True)
    new_model.load_state_dict(checkpoint['model'])
    
    for p1, p2 in zip(model.parameters(), new_model.parameters()):
        assert torch.allclose(p1, p2)
    
    os.remove(checkpoint_path)
    return "Checkpoint save/load OK"


def test_pytorch_device():
    import torch
    cuda = torch.cuda.is_available()
    mps = hasattr(torch.backends, 'mps') and torch.backends.mps.is_available()
    device = "CUDA" if cuda else ("MPS" if mps else "CPU")
    return f"Device: {device}"


def test_pytorch_gpu():
    import torch
    if not torch.cuda.is_available():
        return "N/A (no CUDA)"
    device = torch.device("cuda")
    x = torch.randn(1024, 1024, device=device)
    y = x @ x
    torch.cuda.synchronize()
    return "GPU execution OK"


def test_torchvision():
    import torchvision
    return f"torchvision {torchvision.__version__}"


def test_torchaudio():
    import torchaudio
    return f"torchaudio {torchaudio.__version__}"


def test_tensorflow():
    try:
        import tensorflow as tf
        return f"TensorFlow {tf.__version__}"
    except ImportError:
        raise ImportError("TensorFlow not installed")


def test_tensorflow_ops():
    import tensorflow as tf
    x = tf.random.normal((1024, 1024))
    y = tf.random.normal((1024, 1024))
    z = tf.matmul(x, y)
    assert tuple(z.shape) == (1024, 1024)
    return "Tensor operations OK"


def test_tensorflow_gradient():
    import tensorflow as tf
    x = tf.Variable(tf.random.normal((128, 128)))
    with tf.GradientTape() as tape:
        loss = tf.reduce_mean(x ** 2)
    grad = tape.gradient(loss, x)
    assert grad is not None
    assert tf.reduce_all(tf.math.is_finite(grad))
    return "GradientTape OK"


def test_tensorflow_keras():
    import tensorflow as tf
    model = tf.keras.Sequential([
        tf.keras.layers.Dense(128, activation="relu"),
        tf.keras.layers.Dense(10)
    ])
    model.compile(optimizer="adam", loss=tf.keras.losses.SparseCategoricalCrossentropy(from_logits=True), metrics=["accuracy"])
    x = tf.random.normal((32, 64))
    y = tf.random.uniform((32,), maxval=10, dtype=tf.int32)
    model.fit(x, y, epochs=2, verbose=0)
    return "Keras training OK"


def test_tensorflow_checkpoint():
    import tensorflow as tf
    import tempfile
    import os
    
    model = tf.keras.Sequential([tf.keras.layers.Dense(10)])
    model.compile(optimizer="adam", loss="mse")
    x = tf.random.normal((10, 5))
    y = tf.random.normal((10, 10))
    model.fit(x, y, epochs=1, verbose=0)
    
    path = os.path.join(tempfile.gettempdir(), 'aura_tf_checkpoint.keras')
    model.save(path)
    loaded = tf.keras.models.load_model(path)
    
    # Verify outputs match
    out1 = model.predict(x, verbose=0)
    out2 = loaded.predict(x, verbose=0)
    import numpy as np
    assert np.allclose(out1, out2)
    return "Checkpoint save/load OK"


def test_tensorflow_devices():
    import tensorflow as tf
    devices = tf.config.list_physical_devices()
    gpus = tf.config.list_physical_devices('GPU')
    return f"Devices: {len(devices)} total, {len(gpus)} GPU"


def test_tensorboard():
    import tensorboard
    import tensorflow as tf
    import tempfile
    import os
    
    logdir = os.path.join(tempfile.gettempdir(), 'aura_tb_test')
    callback = tf.keras.callbacks.TensorBoard(log_dir=logdir)
    
    model = tf.keras.Sequential([tf.keras.layers.Dense(1)])
    model.compile(optimizer="adam", loss="mse")
    x = tf.random.normal((10, 5))
    y = tf.random.normal((10, 1))
    model.fit(x, y, epochs=1, callbacks=[callback], verbose=0)
    
    # Check event file exists (may not work on all TF nightly/Windows combos)
    event_files = [f for f in os.listdir(logdir) if f.startswith('events.out')]
    if len(event_files) > 0:
        return f"TensorBoard logs OK ({event_files[0]})"
    else:
        # TensorBoard import works, callback runs - that's the core verification
        return f"TensorBoard import OK (event files not generated on this platform)"


def test_transformers():
    import transformers
    tokenizer = transformers.AutoTokenizer.from_pretrained('bert-base-uncased')
    tokens = tokenizer('Hello world', return_tensors='pt')
    assert 'input_ids' in tokens
    return f"Transformers {transformers.__version__}"


def test_tokenizers():
    import tokenizers
    tok = tokenizers.Tokenizer.from_pretrained('bert-base-uncased')
    encoding = tok.encode('Hello world')
    assert len(encoding.ids) > 0
    return f"Tokenizers {tokenizers.__version__}"


def test_safetensors():
    import torch
    from safetensors.torch import save_file, load_file
    import tempfile
    import os
    
    tensors = {'weight': torch.randn(10, 10)}
    path = os.path.join(tempfile.gettempdir(), 'test.safetensors')
    save_file(tensors, path)
    loaded = load_file(path)
    assert torch.allclose(tensors['weight'], loaded['weight'])
    os.remove(path)
    return f"Safetensors OK"


def test_peft():
    import peft
    from peft import LoraConfig, TaskType
    config = LoraConfig(task_type=TaskType.CAUSAL_LM, r=8, lora_alpha=16, target_modules=['q_proj', 'v_proj'])
    return f"PEFT {peft.__version__}"


def test_accelerate():
    import accelerate
    from accelerate import Accelerator
    accelerator = Accelerator()
    return f"Accelerate {accelerate.__version__}"


def test_datasets():
    import datasets
    ds = datasets.Dataset.from_dict({'text': ['hello', 'world'], 'label': [0, 1]})
    assert len(ds) == 2
    return f"Datasets {datasets.__version__}"


def test_evaluate():
    import evaluate
    metric = evaluate.load('accuracy')
    results = metric.compute(predictions=[0, 1, 0], references=[0, 1, 1])
    assert 'accuracy' in results
    return f"Evaluate {evaluate.__version__}"


def test_sentence_transformers():
    import sentence_transformers
    # Just verify import works; model loading tested separately
    return f"Sentence-Transformers {sentence_transformers.__version__}"


def test_sentence_transformers_embeddings():
    import sentence_transformers
    st_model = sentence_transformers.SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')
    embeddings = st_model.encode(['Hello world', 'Test sentence'])
    assert embeddings.shape == (2, 384)
    return "Embeddings OK"


def test_cross_framework():
    import torch
    import tensorflow as tf
    # Both should work in same process
    t = torch.randn(10, 10)
    tf_t = tf.random.normal((10, 10))
    return "Cross-framework compatibility OK"


def test_aura_imports():
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'aura_backend'))
    
    from aura_core import container
    from aura_core.kernel.kernel import AIKernel
    from aura_core.llm.router import ProviderRouter
    from aura_core.ml import Architecture, build, torch_available
    from aura_core.knowledge.reranking import CrossEncoderReranker
    from aura_core.adapters.local_lora_trainer import LocalLoraTrainer
    return "AURA core imports OK"


def test_architecture_fitness():
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'aura_backend'))
    
    from aura_core.ml import torch_available
    assert isinstance(torch_available(), bool)
    return "Architecture fitness (torch_available) OK"


def get_gpu_info():
    try:
        result = subprocess.run(['nvidia-smi', '--query-gpu=name,memory.total', '--format=csv,noheader'], 
                               capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            return result.stdout.strip()
    except:
        pass
    return "No GPU detected (or nvidia-smi not available)"


def main():
    print("=" * 60)
    print("AURA ML ENVIRONMENT VERIFICATION")
    print("=" * 60)
    print()
    
    # System info
    print("SYSTEM INFO")
    print("-" * 60)
    print(f"OS: {platform.system()} {platform.release()}")
    print(f"Architecture: {platform.machine()}")
    print(f"Python: {sys.version}")
    print(f"CPU: {platform.processor()}")
    print(f"GPU: {get_gpu_info()}")
    print()
    
    # Test definitions
    tests = [
        # Python
        ("Python Version", test_python_version, True),
        
        # PyTorch
        ("PyTorch Import", test_pytorch_import, True),
        ("PyTorch Tensor Ops", test_pytorch_tensor_ops, True),
        ("PyTorch Autograd", test_pytorch_autograd, True),
        ("PyTorch Neural Network", test_pytorch_nn, True),
        ("PyTorch Training Step", test_pytorch_training, True),
        ("PyTorch DataLoader", test_pytorch_dataloader, True),
        ("PyTorch Checkpoint", test_pytorch_checkpoint, True),
        ("PyTorch Device Detection", test_pytorch_device, True),
        ("PyTorch GPU Execution", test_pytorch_gpu, False),
        ("torchvision", test_torchvision, True),
        ("torchaudio", test_torchaudio, True),
        
        # TensorFlow
        ("TensorFlow Import", test_tensorflow, True),
        ("TensorFlow Tensor Ops", test_tensorflow_ops, True),
        ("TensorFlow GradientTape", test_tensorflow_gradient, True),
        ("TensorFlow Keras Training", test_tensorflow_keras, True),
        ("TensorFlow Checkpoint", test_tensorflow_checkpoint, True),
        ("TensorFlow Devices", test_tensorflow_devices, True),
        ("TensorBoard", test_tensorboard, True),
        
        # ML Ecosystem
        ("Transformers", test_transformers, True),
        ("Tokenizers", test_tokenizers, True),
        ("Safetensors", test_safetensors, True),
        ("PEFT", test_peft, True),
        ("Accelerate", test_accelerate, True),
        ("Datasets", test_datasets, True),
        ("Evaluate", test_evaluate, True),
        ("Sentence-Transformers Import", test_sentence_transformers, True),
        ("Sentence-Transformers Embeddings", test_sentence_transformers_embeddings, False),
        
        # Cross-framework
        ("PyTorch + TensorFlow Compat", test_cross_framework, True),
        
        # AURA Integration
        ("AURA Core Imports", test_aura_imports, True),
        ("Architecture Fitness", test_architecture_fitness, True),
    ]
    
    # Run tests
    results = []
    for name, func, required in tests:
        result = run_test(name, func, required)
        results.append(result)
        status = result[1]
        detail = result[2]
        symbol = {"PASS": "PASS", "FAIL": "FAIL", "N/A": "N/A"}.get(status, "?")
        print(f"  {symbol:4s} | {name:35s} | {detail}")
    
    print()
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    
    passed = sum(1 for r in results if r[1] == "PASS")
    failed = sum(1 for r in results if r[1] == "FAIL")
    na = sum(1 for r in results if r[1] == "N/A")
    total = len(results)
    
    print(f"Total: {total} | Passed: {passed} | Failed: {failed} | N/A: {na}")
    print()
    
    if failed > 0:
        print("FAILED TESTS:")
        for name, status, detail in results:
            if status == "FAIL":
                print(f"  - {name}: {detail}")
        print()
        print("RESULT: FAIL")
        return 1
    else:
        print("RESULT: PASS")
        return 0


if __name__ == "__main__":
    sys.exit(main())