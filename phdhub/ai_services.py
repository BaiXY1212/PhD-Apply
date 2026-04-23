"""LLM-related classification and URL validation services."""

import json
import os
import re
import urllib.request

import google.generativeai as genai
from openai import OpenAI


_GEMINI_MODEL = "gemini-2.5-flash"


def _strip_code_fence(text):
    text = (text or "").strip()
    if text.startswith("```json"):
        text = text[len("```json") :].strip()
    if text.startswith("```"):
        text = text[len("```") :].strip()
    if text.endswith("```"):
        text = text[: -len("```")].strip()
    return text


def _is_english_mode(config):
    return str((config or {}).get("app_lang", "zh-CN")).lower().startswith("en")


def _call_gemini_with_fallback(prompt, api_key, temperature=0.3):
    if "HTTP_PROXY" not in os.environ and "HTTPS_PROXY" not in os.environ:
        os.environ["HTTP_PROXY"] = "http://127.0.0.1:7890"
        os.environ["HTTPS_PROXY"] = "http://127.0.0.1:7890"
    genai.configure(api_key=api_key)
    try:
        model = genai.GenerativeModel(_GEMINI_MODEL)
        resp = model.generate_content(
            prompt,
            generation_config={"temperature": temperature},
        )
        return True, (resp.text or "").strip(), _GEMINI_MODEL
    except Exception as e:
        return False, f"{_GEMINI_MODEL}: {e}", ""


def _call_llm(prompt, config, temperature=0.3):
    provider = config.get("ai_provider", "通义千问 (Qwen)")
    if provider == "通义千问 (Qwen)":
        api_key = config.get("qwen_api_key")
        if not api_key:
            return False, "Missing Qwen API Key"
        client = OpenAI(api_key=api_key, base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1")
        completion = client.chat.completions.create(
            model="qwen-plus",
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
        )
        return True, (completion.choices[0].message.content or "").strip()

    api_key = config.get("gemini_api_key")
    if not api_key:
        return False, "Missing Gemini API Key"
    ok, text_or_error, _model_name = _call_gemini_with_fallback(prompt, api_key, temperature=temperature)
    if not ok:
        return False, text_or_error
    return True, text_or_error


def generate_resume_analysis(resume_text, config):
    if not resume_text or len(resume_text.strip()) < 50:
        return False, {}, "Resume text too short"

    clipped = resume_text[:10000]
    app_lang = str(config.get("app_lang", "zh-CN")).lower()
    if app_lang.startswith("en"):
        prompt = f"""You are a PhD application advisor. Analyze the applicant strictly based on the resume text below.

Resume Text:
{clipped}

Please return JSON only (no markdown):
{{
  "strengths": ["Strength 1", "Strength 2", "Strength 3"],
  "weaknesses": ["Weakness 1", "Weakness 2", "Weakness 3"],
  "improvements": ["Actionable improvement 1", "Actionable improvement 2", "Actionable improvement 3", "Actionable improvement 4"]
}}

Language requirement:
- All analysis content must be in English.
"""
    else:
        prompt = f"""你是博士申请顾问。请严格基于下面简历内容分析该申请者申博情况。

简历文本：
{clipped}

请仅返回 JSON（不要markdown）：
{{
  "strengths": ["优势1", "优势2", "优势3"],
  "weaknesses": ["劣势1", "劣势2", "劣势3"],
  "improvements": ["可执行改进1", "可执行改进2", "可执行改进3", "可执行改进4"]
}}

语言要求：
- 所有分析内容必须使用中文。
"""
    try:
        ok, ans = _call_llm(prompt, config, temperature=0.2)
        if not ok:
            return False, {}, ans
        parsed = json.loads(_strip_code_fence(ans))
        return True, parsed, ans
    except Exception as e:
        return False, {}, f"Exception: {str(e)}"


def generate_rp_analysis(rp_text, config):
    if not rp_text or len(rp_text.strip()) < 80:
        return False, {}, "RP text too short"

    clipped = rp_text[:12000]
    app_lang = str(config.get("app_lang", "zh-CN")).lower()
    if app_lang.startswith("en"):
        prompt = f"""You are a PhD application writing advisor. Analyze the quality strictly based on the RP text below.

RP Text:
{clipped}

Please return JSON only (no markdown):
{{
  "good_points": ["Strong point 1", "Strong point 2", "Strong point 3"],
  "weaknesses": ["Issue 1", "Issue 2", "Issue 3"],
  "improvements": ["Improvement 1", "Improvement 2", "Improvement 3", "Improvement 4"]
}}

Language requirement:
- All analysis content must be in English.
"""
    else:
        prompt = f"""你是博士申请文书顾问。请严格基于下面 RP 文本分析质量。

RP文本：
{clipped}

请仅返回 JSON（不要markdown）：
{{
  "good_points": ["写得好的点1", "写得好的点2", "写得好的点3"],
  "weaknesses": ["缺陷1", "缺陷2", "缺陷3"],
  "improvements": ["改进建议1", "改进建议2", "改进建议3", "改进建议4"]
}}

语言要求：
- 所有分析内容必须使用中文。
"""
    try:
        ok, ans = _call_llm(prompt, config, temperature=0.2)
        if not ok:
            return False, {}, ans
        parsed = json.loads(_strip_code_fence(ans))
        return True, parsed, ans
    except Exception as e:
        return False, {}, f"Exception: {str(e)}"


def generate_interview_advice(
    prof_name,
    univ_name,
    research_direction,
    homepage_url,
    homepage_text,
    papers,
    resume_text,
    config,
):
    if not resume_text or len(resume_text.strip()) < 50:
        return False, [], "Resume text missing or too short"

    paper_lines = []
    for i, p in enumerate(papers[:10], start=1):
        title = (p or {}).get("title", "")
        year = (p or {}).get("year", "")
        venue = (p or {}).get("venue", "")
        line = f"{i}. {title}"
        meta = " | ".join(x for x in [year, venue] if x)
        if meta:
            line += f" ({meta})"
        paper_lines.append(line)

    if _is_english_mode(config):
        prompt = f"""You are a PhD interview coach. Based on the candidate resume and professor profile, generate targeted interview preparation advice.

Professor: {prof_name}
University: {univ_name}
Research Direction: {research_direction}
Homepage URL: {homepage_url}
Homepage Summary (may be incomplete):
{(homepage_text or "")[:3000]}

Professor Papers (optional):
{chr(10).join(paper_lines)}

Candidate Resume:
{resume_text[:9000]}

Return JSON only (no markdown):
{{
  "advice": [
    "Advice 1",
    "Advice 2",
    "Advice 3",
    "Advice 4",
    "Advice 5",
    "Advice 6"
  ]
}}

Language requirement:
- All advice must be in English.
"""
    else:
        prompt = f"""你是博士面试教练。请主要基于候选人简历与导师研究方向，生成有针对性的面试准备建议。

导师：{prof_name}
学校：{univ_name}
导师方向：{research_direction}
主页链接：{homepage_url}
主页文本摘要（可能不完整）：
{(homepage_text or "")[:3000]}

导师论文信息（可选参考）：
{chr(10).join(paper_lines)}

候选人简历：
{resume_text[:9000]}

请仅返回 JSON（不要markdown）：
{{
  "advice": [
    "建议1（你简历中最该强调的匹配点）",
    "建议2（导师可能关注的能力与证据）",
    "建议3（高风险追问与应对）",
    "建议4（补短板的准备动作）",
    "建议5（3分钟自我介绍结构）",
    "建议6（结尾提问策略）"
  ]
}}

语言要求：
- 所有建议内容必须使用中文。
"""
    try:
        ok, ans = _call_llm(prompt, config, temperature=0.3)
        if not ok:
            return False, [], ans
        parsed = json.loads(_strip_code_fence(ans))
        advice = parsed.get("advice", [])
        if not isinstance(advice, list):
            return False, [], ans
        advice = [str(x).strip() for x in advice if str(x).strip()]
        if not advice:
            return False, [], ans
        return True, advice, ans
    except Exception as e:
        return False, [], f"Exception: {str(e)}"


def generate_interview_questions(prof_name, univ_name, research_direction, papers, config):
    """
    Return tuple: (success: bool, questions: list[str], raw_or_error: str)
    """
    paper_lines = []
    for i, p in enumerate((papers or [])[:5], start=1):
        title = (p or {}).get("title", "")
        if title:
            paper_lines.append(f"{i}. {title}")

    if _is_english_mode(config):
        prompt = f"""You are a PhD interview coach.
Generate 5 high-frequency, realistic interview questions (not paper-by-paper details).

Professor: {prof_name}
University: {univ_name}
Research Direction: {research_direction}

Optional paper titles:
{chr(10).join(paper_lines)}

Requirements:
1) Return strict JSON only, no markdown and no extra text.
2) JSON format:
{{
  "questions": [
    "Q1 ...",
    "... total 5 ..."
  ]
}}
3) Questions should cover: motivation, method capability, project evidence, future plan, and fit with professor direction.
4) Keep the wording natural like real interviewers.

Language requirement:
- All questions must be in English.
"""
    else:
        prompt = f"""你是一位博士申请面试教练。
请生成“导师面试中最可能问到”的 5 个高频综合问题（不是逐篇论文细节提问）。

导师：{prof_name}
学校：{univ_name}
研究方向：{research_direction}

可参考的部分论文标题：
{chr(10).join(paper_lines)}

要求：
1) 输出严格 JSON，不要 markdown，不要多余文字。
2) JSON 格式：
{{
  "questions": [
    "Q1 ...（高频综合问题）",
    "... 共5条 ..."
  ]
}}
3) 问题要覆盖：研究动机、方法能力、项目经历、未来规划、与导师方向匹配。
4) 每条问题要像真实面试官会问的口吻。

语言要求：
- 所有问题内容必须使用中文。
"""

    try:
        provider = config.get("ai_provider", "通义千问 (Qwen)")
        ans = ""
        if provider == "通义千问 (Qwen)":
            api_key = config.get("qwen_api_key")
            if not api_key:
                return False, [], "Missing Qwen API Key"
            client = OpenAI(api_key=api_key, base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1")
            completion = client.chat.completions.create(
                model="qwen-plus",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
            )
            ans = completion.choices[0].message.content.strip()
        else:
            api_key = config.get("gemini_api_key")
            if not api_key:
                return False, [], "Missing Gemini API Key"
            ok, ans_or_error, _model_name = _call_gemini_with_fallback(prompt, api_key, temperature=0.3)
            if not ok:
                return False, [], ans_or_error
            ans = ans_or_error

        clean = _strip_code_fence(ans)
        parsed = json.loads(clean)
        questions = parsed.get("questions", [])
        if not isinstance(questions, list):
            return False, [], ans
        questions = [str(q).strip() for q in questions if str(q).strip()]
        if not questions:
            return False, [], ans
        return True, questions[:5], ans
    except Exception as e:
        return False, [], f"Exception: {str(e)}"


def generate_high_frequency_answer(
    question,
    prof_name,
    univ_name,
    research_direction,
    homepage_url,
    homepage_text,
    papers,
    resume_text,
    config,
):
    if not question or not str(question).strip():
        return False, {}, "Question missing"
    if not resume_text or len(str(resume_text).strip()) < 50:
        return False, {}, "Resume text missing or too short"

    paper_lines = []
    for i, p in enumerate((papers or [])[:10], start=1):
        title = (p or {}).get("title", "")
        year = (p or {}).get("year", "")
        venue = (p or {}).get("venue", "")
        line = f"{i}. {title}"
        meta = " | ".join(x for x in [year, venue] if x)
        if meta:
            line += f" ({meta})"
        paper_lines.append(line)

    if _is_english_mode(config):
        prompt = f"""You are a PhD interview coach. For the high-frequency interview question below, provide a reusable answer template with concrete evidence.

Question:
{str(question).strip()}

Professor: {prof_name}
University: {univ_name}
Research Direction: {research_direction}
Homepage URL: {homepage_url}
Homepage Summary (may be incomplete):
{(homepage_text or "")[:2500]}

Candidate Resume Summary:
{(resume_text or "")[:8500]}

Recent papers (optional):
{chr(10).join(paper_lines) if paper_lines else "None"}

Requirements:
1) The answer must be concrete and include methods, evidence, outcomes, or quantifiable details.
2) Avoid vague statements. Keep answer length around 90-170 English words.
3) Also provide 3-5 short key bullet points.
4) Return JSON only, no markdown:
{{
  "suggested_answer": "One polished answer paragraph",
  "key_points": ["Point 1", "Point 2", "Point 3"]
}}

Language requirement:
- All output content must be in English.
"""
    else:
        prompt = f"""你是博士面试辅导教练。请针对一个高频考察问题，给候选人一个可直接复述、同时有细节证据的回答模板。

问题：
{str(question).strip()}

导师：{prof_name}
学校：{univ_name}
研究方向：{research_direction}
主页链接：{homepage_url}
主页摘要（可不完整）：
{(homepage_text or "")[:2500]}

候选人简历摘要：
{(resume_text or "")[:8500]}

导师近期论文（可选）：
{chr(10).join(paper_lines) if paper_lines else "无"}

要求：
1) 回答要具体，包含方法、证据、结果或量化信息。
2) 避免空话，长度控制在 130~220 中文字。
3) 再给 3-5 条答题要点（短句）。
4) 只返回 JSON，不要 markdown：
{{
  "suggested_answer": "可直接用于面试回答的一段话",
  "key_points": ["要点1", "要点2", "要点3"]
}}

语言要求：
- 所有输出内容必须使用中文。
"""
    try:
        ok, ans = _call_llm(prompt, config, temperature=0.35)
        if not ok:
            return False, {}, ans
        parsed = json.loads(_strip_code_fence(ans))
        suggested_answer = str(parsed.get("suggested_answer", "")).strip()
        key_points = parsed.get("key_points", [])
        if not isinstance(key_points, list):
            key_points = []
        key_points = [str(x).strip() for x in key_points if str(x).strip()]
        if not suggested_answer:
            return False, {}, ans
        return True, {"suggested_answer": suggested_answer, "key_points": key_points[:5]}, ans
    except Exception as e:
        return False, {}, f"Exception: {str(e)}"


def generate_mock_interview_turn(
    prof_name,
    univ_name,
    research_direction,
    homepage_url,
    homepage_text,
    resume_text,
    conversation,
    config,
):
    en_mode = _is_english_mode(config)
    convo_lines = []
    for turn in (conversation or [])[-12:]:
        role = str((turn or {}).get("role", ""))
        content = str((turn or {}).get("content", "")).strip()
        if not content:
            continue
        if role == "candidate":
            convo_lines.append(f"{'Candidate' if en_mode else '候选人'}: {content}")
        else:
            convo_lines.append(f"{'Interviewer' if en_mode else '面试官'}: {content}")

    if en_mode:
        prompt = f"""You are a PhD interviewer. Continue a realistic interview dialogue.
You should ask follow-up questions based on weaknesses, missing evidence, method details, and fit.

Professor: {prof_name}
University: {univ_name}
Research Direction: {research_direction}
Homepage URL: {homepage_url}
Homepage Summary (may be incomplete):
{(homepage_text or "")[:3000]}

Candidate Resume Summary:
{(resume_text or "")[:9000]}

Current conversation (chronological):
{chr(10).join(convo_lines)}

Requirements:
1) Output only the interviewer's next turn, up to 2-4 sentences.
2) Prioritize probing weak points, evidence gaps, method details, and fit.
3) Do not score, do not summarize, do not output markdown.
4) Strict JSON output:
{{
  "reply": "Interviewer next turn"
}}

Language requirement:
- Output must be in English.
"""
    else:
        prompt = f"""你是博士导师面试官，请模拟真实面试对话。
你要根据导师信息和候选人简历，持续追问并评估其匹配度。

导师：{prof_name}
学校：{univ_name}
研究方向：{research_direction}
主页链接：{homepage_url}
主页摘要（可不完整）：
{(homepage_text or "")[:3000]}

候选人简历摘要：
{(resume_text or "")[:9000]}

当前对话（按时间顺序）：
{chr(10).join(convo_lines)}

要求：
1) 你只输出“面试官下一轮发言”，一次最多 2-4 句话。
2) 优先追问候选人回答中的薄弱点、证据不足点、方法细节和匹配度。
3) 不要给最终评分，不要总结，不要输出markdown。
4) 输出严格 JSON：
{{
  "reply": "面试官下一轮发言"
}}

语言要求：
- 输出必须使用中文。
"""
    try:
        ok, ans = _call_llm(prompt, config, temperature=0.5)
        if not ok:
            return False, "", ans
        parsed = json.loads(_strip_code_fence(ans))
        reply = str(parsed.get("reply", "")).strip()
        if not reply:
            return False, "", ans
        return True, reply, ans
    except Exception as e:
        return False, "", f"Exception: {str(e)}"

def evaluate_mock_interview_session(
    prof_name,
    univ_name,
    research_direction,
    resume_text,
    conversation,
    config,
):
    en_mode = _is_english_mode(config)
    convo_lines = []
    for turn in (conversation or []):
        role = str((turn or {}).get("role", ""))
        content = str((turn or {}).get("content", "")).strip()
        if not content:
            continue
        if role == "candidate":
            convo_lines.append(f"{'Candidate' if en_mode else '候选人'}: {content}")
        else:
            convo_lines.append(f"{'Interviewer' if en_mode else '面试官'}: {content}")

    if en_mode:
        prompt = f"""You are a PhD interview evaluator. Based on the mock interview dialogue below, provide scores and admission tendency.

Professor: {prof_name}
University: {univ_name}
Research Direction: {research_direction}

Candidate Resume Summary:
{(resume_text or "")[:8000]}

Mock Interview Dialogue:
{chr(10).join(convo_lines[:80])}

Return strict JSON only (no markdown):
{{
  "overall_score": 0,
  "dimension_scores": {{
    "research_fit": 0,
    "method_depth": 0,
    "communication": 0,
    "potential": 0
  }},
  "admission_tendency": "Strongly Recommend/Recommend/Pending/Not Recommend",
  "summary": "1-2 sentence overall summary",
  "strengths": ["Strength 1", "Strength 2", "Strength 3"],
  "weaknesses": ["Weakness 1", "Weakness 2", "Weakness 3"],
  "improvements": ["Improvement 1", "Improvement 2", "Improvement 3"]
}}

Scoring rules:
- All scores are integers from 0-100.
- overall_score reflects overall admission competitiveness.
- admission_tendency must be one of: Strongly Recommend, Recommend, Pending, Not Recommend.

Language requirement:
- All textual fields must be in English.
"""
    else:
        prompt = f"""你是博士申请面试评委，请基于以下模拟面试对话给出评分与录取倾向。

导师：{prof_name}
学校：{univ_name}
研究方向：{research_direction}

候选人简历摘要：
{(resume_text or "")[:8000]}

模拟面试对话：
{chr(10).join(convo_lines[:80])}

请严格返回 JSON（不要markdown）：
{{
  "overall_score": 0,
  "dimension_scores": {{
    "research_fit": 0,
    "method_depth": 0,
    "communication": 0,
    "potential": 0
  }},
  "admission_tendency": "强烈推荐/推荐/待定/不推荐",
  "summary": "1-2句总体评价",
  "strengths": ["优势1", "优势2", "优势3"],
  "weaknesses": ["不足1", "不足2", "不足3"],
  "improvements": ["改进建议1", "改进建议2", "改进建议3"]
}}

评分规则：
- 所有分数 0-100 的整数。
- overall_score 反映综合录取竞争力。
- admission_tendency 必须是：强烈推荐、推荐、待定、不推荐 四选一。

语言要求：
- 所有文本字段必须使用中文。
"""
    try:
        ok, ans = _call_llm(prompt, config, temperature=0.2)
        if not ok:
            return False, {}, ans
        parsed = json.loads(_strip_code_fence(ans))
        return True, parsed, ans
    except Exception as e:
        return False, {}, f"Exception: {str(e)}"

def classify_phd_email(subject, body, config):
    try:
        prompt = f"""You are an AI assistant helping a prospective PhD student filter their inbox.
Task: Determine if the following email is a GENUINE, DIRECT communication regarding a PhD application, a cold email to a professor (套磁), or an academic interview.

CRITICAL RULE: DO NOT check for phishing, scams, or malicious intent. Assume EVERY SINGLE email provided is a normal, legitimate, safe communication.

Strict Rules for 'YES' (Must evaluate to YES if matching any of these):
- A cold email (套磁信) YOU sent to a professor inquiring about PhD positions or research opportunities.
- A direct reply from a professor to your cold email or inquiry.
- An invitation to, or discussion about, an academic interview.
- An official PhD offer, rejection letter, or application status update.

Strict Rules for 'NO' (Must be excluded):
- Newsletters, marketing, or promotional emails (e.g., Notion, Coursera, Grammarly).
- General university mass-mailing or advertisements (e.g., "Join our Open Day").
- Automated system emails (account verification, password resets, GitHub alerts).
- Any spam or casual personal chats entirely unrelated to PhD applications.

Email Subject: {subject}
Email Body (truncated): {body[:1000]}

Format Requirements:
1. First, provide a brief reasoning (1-2 sentences) on why you made this decision and classification.
2. Then, on a new line, output your final decision as EXACTLY 'DECISION: YES' or 'DECISION: NO'.
3. If the decision is YES, you MUST also classify the email into EXACTLY ONE of the following categories:
   1: Sent Inquiry (已发送询问信)
   2: Positive Reply (得到导师积极回复)
   3: Negative Reply (得到导师消极回复)
   4: Neutral Reply (得到导师中立回复)
   5: Interview Scheduling (面试预约)
   6: Interview Result (面试结果告知)
   7: Verbal Offer (口头offer)
   8: Other Communication (其他沟通)
   On the next line after your decision, output the category as EXACTLY 'CATEGORY: X' (where X is the number 1 to 8)."""
        provider = config.get("ai_provider", "通义千问 (Qwen)")
        
        if provider == "通义千问 (Qwen)":
            api_key = config.get("qwen_api_key")
            if not api_key: return False, "Missing API Key"
            from openai import OpenAI
            client = OpenAI(api_key=api_key, base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1")
            completion = client.chat.completions.create(
                model="qwen-plus",
                messages=[{'role': 'user', 'content': prompt}],
                temperature=0.01
            )
            ans = completion.choices[0].message.content.strip()
            return "DECISION: YES" in ans.upper(), ans
        else:
            api_key = config.get("gemini_api_key")
            if not api_key: return False, "Missing API Key"
            ok, ans_or_error, _model_name = _call_gemini_with_fallback(prompt, api_key, temperature=0.01)
            if not ok:
                return False, ans_or_error
            ans = ans_or_error
            return "DECISION: YES" in ans.upper(), ans
    except Exception as e:
        return False, f"Exception: {str(e)}"

def extract_category(ans):
    import re
    match = re.search(r"CATEGORY:\s*([1-8])", ans, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return None

def verify_professor_homepage(url, prof_email, config):
    try:
        import urllib.request
        import re
        import json
        
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/100.0.4896.75 Safari/537.36'})
        try:
            html = urllib.request.urlopen(req, timeout=8).read().decode('utf-8', errors='ignore')
        except Exception as net_e:
            return {"is_real_homepage": False, "reasoning": f"Network Error: {str(net_e)}", "scraped_text": ""}
            
        text = re.sub(r'<style.*?>.*?</style>', '', html, flags=re.DOTALL|re.IGNORECASE)
        text = re.sub(r'<script.*?>.*?</script>', '', text, flags=re.DOTALL|re.IGNORECASE)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()
        
        prompt = f'''You are a strict academic verifier.
The following text was extracted from this URL claimed to be a professor's academic homepage: {url}
The email associated with this lookup is: {prof_email}

Webpage text excerpt (first 4000 characters):
{text[:4000]}

Analyze the text and determine:
1. Is this actually an academic/professional homepage profile for a specific researcher/professor? (If it's just a generic university department index, a 404 Not Found, an empty domain registrar page, or access denied error, respond with "is_real_homepage": false).
2. If valid, briefly summarize their core research domains (keywords).
3. If invalid, briefly explain why in the reasoning.

Provide your response EXACTLY in JSON format without markdown code blocks:
{{
    "is_real_homepage": true or false,
    "research_keywords": "comma-separated keywords or None",
    "reasoning": "A 1-sentence explanation"
}}'''
        
        provider = config.get("ai_provider", "通义千问 (Qwen)")
        ans = "{}"
        if provider == "通义千问 (Qwen)":
            api_key = config.get("qwen_api_key")
            if api_key:
                from openai import OpenAI
                client = OpenAI(api_key=api_key, base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1")
                completion = client.chat.completions.create(
                    model="qwen-plus",
                    messages=[{'role': 'user', 'content': prompt}],
                    temperature=0.01
                )
                ans = completion.choices[0].message.content.strip()
        else:
            api_key = config.get("gemini_api_key")
            if api_key:
                ok, ans_or_error, _model_name = _call_gemini_with_fallback(prompt, api_key, temperature=0.01)
                if ok:
                    ans = ans_or_error
                else:
                    return {"is_real_homepage": False, "reasoning": ans_or_error, "scraped_text": ""}
        
        ans = re.sub(r'```json\\n|\\n```|```', '', ans, flags=re.IGNORECASE).strip()
        result = json.loads(ans)
        result["scraped_text"] = text[:800] + "..." if len(text) > 800 else text
        return result
    except Exception as e:
        print(f"Validation failed for {url}: {e}")
        return {"is_real_homepage": False, "reasoning": str(e), "scraped_text": ""}
