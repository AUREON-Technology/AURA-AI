# AURA AI v2.0.0 Offline Agent Configuration Guide

AURA AI supports high-fidelity local inference using local Ollama services when network access is unavailable or quotas are exhausted.

---

## 1. Local Ollama Server Installation

1. **Download Ollama**: Visit [ollama.com](https://ollama.com) and install the native client for your platform (Windows, macOS, or Linux).
2. **Launch Server**: Ensure Ollama is running in your taskbar or terminal. By default, it exposes services on:
   ```http
   http://localhost:11434
   ```

---

## 2. Pulling Required Models
To match the v2.0.0 agent settings, you must download the following model weights:

### 2.1 Fast Offline Agent (Phi-3 / Gemma-2B)
For general chat and fast response loops:
```bash
ollama pull phi3
```

### 2.2 Reasoning Offline Agent (Mistral-7B / Qwen-7B)
For code compilation, task plans, and analytical reasoning:
```bash
ollama pull mistral
```

### 2.3 Premium Offline Agent (Llama3-8B / DeepSeek)
For high-reasoning tasks:
```bash
ollama pull llama3
```

---

## 3. Client Configuration
1. Open AURA AI settings in the chat sidebar.
2. Under **AI Routing Config**, configure your preferred local endpoint URL (defaults to `http://localhost:11434`).
3. Toggle the provider settings:
   - **Auto**: Switches to offline agents automatically if `google.com` cannot be reached.
   - **Offline Fast / Reasoning / Premium**: Forces immediate execution on local model weights without reaching out to the cloud.
