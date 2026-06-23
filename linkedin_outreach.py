#!/usr/bin/env python3
"""
LinkedIn Outreach — generate tailored cold emails + CV adaptations from job posts.

Usage:
  # Paste a job description (then Ctrl+D)
  python3 linkedin_outreach.py

  # From a file
  python3 linkedin_outreach.py --file job_description.txt

  # With custom contact name
  python3 linkedin_outreach.py --contact "Omar Kamoun" --role "HR Manager"

  # Output to a file
  python3 linkedin_outreach.py --file job.txt --output result.txt

  # Just generate the email (skip CV adaptation)
  python3 linkedin_outreach.py --file job.txt --email-only

  # Just CV adaptation
  python3 linkedin_outreach.py --file job.txt --cv-only
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

from openai import OpenAI

MODEL = 'deepseek-v4-flash'
_DEEPSEEK_BASE = os.environ.get('DEEPSEEK_BASE_URL', 'https://api.deepseek.com')

_SENDER = 'Mohammed Bouziyani'
_SENDER_EMAIL = 'mb.bouziyani@gmail.com'
_SENDER_LINKEDIN = 'linkedin.com/in/mohammed-bouziyani'

_BASE_PROFILE = """
Name: Mohammed Bouziyani
Location: Morocco — open to remote worldwide

Experience:
1. Full-stack internship (Feb–May 2025) — Networia
   - Built a task management web application
   - Technologies used varied by project needs
   
2. Full-stack developer contract (Mar–Sep 2024) — Vision Business Consulting
   - Delivered web + mobile apps for enterprise clients
   - Manufacturing and healthcare sectors
   - Achieved 20% performance improvement

3. Full-stack internship (Jun–Aug 2023) — Networia
   - Built a medical practice management system
   - Production-deployed

4. Internship (May–Jul 2022) — FSSM Marrakech
   - Built an HR application

Projects:
- Job Search Agent: multi-agent AI pipeline (Claude API, APScheduler, SQLite, Flask dashboard, Docker Compose, DigitalOcean VPS)
- E-commerce platform: Spring Boot + React + PostgreSQL + Docker

Education: Computer Science & Information Systems Engineering, Université Privée de Marrakech (2024)
Languages: Arabic (native), French (C1), English (B2)
"""

_SYSTEM_EMAIL = """You are an expert career coach and copywriter. Your task is to write a cold outreach email for a job application that is specific, concise, and effective.

Rules:
- The email must be genuine and not lie about years of experience or job titles
- Adapt the technology keywords to match the job description
- Keep the body under 100 words (excluding greeting and sign-off)
- Start with "Hi {firstName}," on its own line
- Reference something specific about the company (not generic praise)
- End with a clear ask: "Would a 15-min call make sense?"
- Sign-off: Mohammed Bouziyani | mb.bouziyani@gmail.com | linkedin.com/in/mohammed-bouziyani
- No filler phrases: "I am passionate about", "I hope this finds you well", "excited to", "I believe"
- Subject must reference the company name

Return JSON: {"subject": "...", "body": "..."}"""

_SYSTEM_CV = """You are an expert resume writer and ATS optimization specialist. Your task is to adapt Mohammed Bouziyani's CV to match a specific job posting.

Rules:
- NEVER change: dates, company names, job titles, education, years of experience
- DO adapt: technology keywords, project descriptions, bullet point wording, order of emphasis
- The goal is to make the CV pass ATS keyword filters while being 100% truthful about experience level
- Rewrite each experience bullet to use the exact tech keywords from the job description where plausible
- For Networia 2025: it was a full-stack role, so any web tech stack fits
- For Vision Consulting: it was enterprise client work, so any enterprise context fits
- For the Job Search Agent project: it's a versatile project that can be described as AI pipeline, DevOps setup, or full-stack architecture depending on the role

Return JSON with:
- Which experience to lead with (order)
- Rewritten bullet points for each experience
- Which project to emphasize
- Any other ATS keyword recommendations"""


def read_job_description(args) -> str:
    """Read job description from file or stdin."""
    if args.file:
        return Path(args.file).read_text(encoding='utf-8')
    else:
        print("Paste the job description (Ctrl+D when done):", file=sys.stderr)
        return sys.stdin.read().strip()


def analyze_job(client: OpenAI, job_desc: str) -> dict:
    """Analyze a job description to extract key info."""
    resp = client.chat.completions.create(
        model=MODEL,
        max_tokens=512,
        temperature=0,
        messages=[
            {'role': 'system', 'content': 'Extract structured info from this job posting. Return JSON.'},
            {'role': 'user', 'content': f"""Analyze this job posting and return JSON with:
- company_name: the company name (or "Unknown" if not clear)
- role_title: the job title
- seniority: junior/mid/senior/lead
- main_stack: array of primary tech keywords (languages, frameworks, tools)
- domain: backend/frontend/full-stack/devops/ai/data/cloud/other
- remote_policy: remote/hybrid/onsite
- key_requirements: array of 5 most important requirements
- role_type_category: choose ONE: JAVA_SPRING / AI_COMPANY / DEVOPS_CLOUD / NODEJS_PYTHON / FRENCH_STARTUP / GENERAL_TECH / FRONTEND / DATA_ENGINEERING

Job posting:
{job_desc[:2000]}"""},
        ],
    )
    text = resp.choices[0].message.content.strip()
    text = re.sub(r'^```(?:json)?\s*', '', text)
    text = re.sub(r'\s*```$', '', text)
    return json.loads(text)


def generate_email(client: OpenAI, job_desc: str, analysis: dict, contact_name: str | None) -> dict:
    """Generate a tailored cold email."""
    company = analysis.get('company_name', 'the company')
    role = analysis.get('role_title', 'the role')
    stack = ', '.join(analysis.get('main_stack', []))
    domain = analysis.get('domain', 'tech')
    category = analysis.get('role_type_category', 'GENERAL_TECH')
    remote = analysis.get('remote_policy', 'remote')
    requirements = '\n'.join(f'- {r}' for r in analysis.get('key_requirements', []))

    profile_for_email = _BASE_PROFILE

    prompt = f"""Write a cold outreach email from Mohammed Bouziyani to {'the hiring team' if not contact_name else contact_name} at {company}.

Company info:
  Role: {role} ({seniority_level})
  Stack: {stack}
  Domain: {domain}
  Remote: {remote}
  Category: {category}
  Key requirements:
{requirements}

Mohammed's profile:
{profile_for_email}

Email structure — follow EXACTLY in this order:
0. Greeting — "Hi {{firstName}}," on its own line
1. Hook: One short sentence (≤15 words). Reference something specific about {company} or their product/stack — show you did your research. NOT generic.
2. Relevance: One short sentence (≤20 words) connecting Mohammed's experience to their needs. Use the STACK KEYWORDS from the job description.
3. Proof: One short sentence with a concrete achievement. Use tech keywords from the job description.
4. Ask: "Would a 15-min call make sense?"
5. Sign-off: {_SENDER} | {_SENDER_EMAIL} | {_SENDER_LINKEDIN}

Hard rules:
- Body (excluding greeting and sign-off) ≤ 100 words
- Use tech keywords from the job description naturally — don't force them
- No filler: "I am passionate about", "I hope this finds you well", "excited to", "I believe"
- NEVER claim years of experience you don't have
- Be humble and direct — you're a junior developer looking for an opportunity
- Subject must reference {company} specifically

Return exactly this JSON:
{{"subject": "...", "body": "..."}}"""

    resp = client.chat.completions.create(
        model=MODEL,
        max_tokens=2048,
        temperature=0.2,
        messages=[
            {'role': 'system', 'content': _SYSTEM_EMAIL},
            {'role': 'user', 'content': prompt},
        ],
    )
    text = resp.choices[0].message.content.strip()
    text = re.sub(r'^```(?:json)?\s*', '', text)
    text = re.sub(r'\s*```$', '', text)
    return json.loads(text)


def generate_cv_adaptation(client: OpenAI, job_desc: str, analysis: dict) -> dict:
    """Generate CV adaptation recommendations."""
    company = analysis.get('company_name', 'the company')
    role = analysis.get('role_title', 'the role')
    stack = ', '.join(analysis.get('main_stack', []))
    domain = analysis.get('domain', 'tech')
    category = analysis.get('role_type_category', 'GENERAL_TECH')
    requirements = '\n'.join(f'- {r}' for r in analysis.get('key_requirements', []))

    prompt = f"""Adapt Mohammed Bouziyani's CV for this specific job posting.

JOB:
  Company: {company}
  Role: {role}
  Stack keywords: {stack}
  Domain: {domain}
  Category: {category}
  Key requirements:
{requirements}

BASE CV:
{_BASE_PROFILE}

Return JSON with:
{{
  "lead_experience": "Which experience/project to put first (1-2 sentences)",
  "cv_bullets": {{
    "networia_2025": ["rewritten bullet 1 using job keywords", "bullet 2..."],
    "vision_consulting_2024": ["rewritten bullet 1", "bullet 2..."],
    "networia_2023": ["rewritten bullet 1", "bullet 2..."],
    "fssm_2022": ["rewritten bullet 1", "bullet 2..."]
  }},
  "project_to_emphasize": "Which project and how to describe it",
  "ats_keywords_to_include": ["keyword1", "keyword2", ...],
  "experience_order": ["which experience first", "second", ...],
  "notes": "Any other ATS tips for this specific job"
}}

RULES:
- NEVER change dates, company names, job titles, or education
- DO rewrite technology keywords to match the job description
- For Networia 2025: was full-stack — can emphasize any web tech
- For Vision Consulting: was enterprise client work — can emphasize any enterprise-relevant tech
- For Job Agent project: can be positioned as AI, DevOps, or full-stack
- Be specific and concrete — no vague buzzwords"""

    resp = client.chat.completions.create(
        model=MODEL,
        max_tokens=2048,
        temperature=0.2,
        messages=[
            {'role': 'system', 'content': _SYSTEM_CV},
            {'role': 'user', 'content': prompt},
        ],
    )
    text = resp.choices[0].message.content.strip()
    text = re.sub(r'^```(?:json)?\s*', '', text)
    text = re.sub(r'\s*```$', '', text)
    return json.loads(text)


def print_result(analysis: dict, email: dict | None, cv: dict | None, contact_name: str | None):
    """Pretty-print the results."""
    company = analysis.get('company_name', 'Unknown')
    role = analysis.get('role_title', 'Unknown')

    print(f"\n{'='*60}")
    print(f"  📋 {company} — {role}")
    print(f"  Stack: {', '.join(analysis.get('main_stack', []))}")
    print(f"  Domain: {analysis.get('domain', '?')}  |  Remote: {analysis.get('remote_policy', '?')}")
    print(f"{'='*60}\n")

    if email:
        print(f"{'─'*60}")
        print(f"  📧 EMAIL")
        print(f"{'─'*60}")
        print(f"  Subject: {email['subject']}\n")
        print(f"{email['body']}")
        print()

    if cv:
        print(f"{'─'*60}")
        print(f"  📄 CV ADAPTATION")
        print(f"{'─'*60}")
        print(f"\n  → Lead with: {cv.get('lead_experience', '')}\n")
        print(f"  → Experience order: {', '.join(cv.get('experience_order', []))}\n")

        print(f"  → Rewritten bullets:")
        for exp, bullets in cv.get('cv_bullets', {}).items():
            if bullets:
                print(f"    {exp}:")
                for b in bullets:
                    print(f"      • {b}")

        print(f"\n  → Project to emphasize: {cv.get('project_to_emphasize', '')}")

        print(f"\n  → ATS keywords: {', '.join(cv.get('ats_keywords_to_include', []))}")

        if cv.get('notes'):
            print(f"\n  → Notes: {cv['notes']}")

    print(f"\n{'='*60}\n")


def main():
    parser = argparse.ArgumentParser(description='Generate tailored LinkedIn outreach')
    parser.add_argument('--file', '-f', type=str, help='File containing job description')
    parser.add_argument('--contact', type=str, help='Contact/recruiter name')
    parser.add_argument('--role', type=str, help='Contact role (e.g. HR Manager)')
    parser.add_argument('--email-only', action='store_true', help='Generate email only')
    parser.add_argument('--cv-only', action='store_true', help='Generate CV adaptation only')
    parser.add_argument('--output', '-o', type=str, help='Save output to file')
    args = parser.parse_args()

    api_key = os.environ.get('DEEPSEEK_API_KEY')
    if not api_key:
        api_key = os.environ.get('OPENAI_API_KEY')
    if not api_key:
        print("Error: DEEPSEEK_API_KEY or OPENAI_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    client = OpenAI(api_key=api_key, base_url=_DEEPSEEK_BASE)

    # Read job description
    job_desc = read_job_description(args)
    if not job_desc:
        print("Error: No job description provided", file=sys.stderr)
        sys.exit(1)

    # Analyze
    print("🔍 Analyzing job posting...", file=sys.stderr)
    analysis = analyze_job(client, job_desc)

    # Build contact name
    contact_name = None
    if args.contact:
        contact_name = args.contact
        if args.role:
            contact_name = f"{args.contact} ({args.role})"

    # Generate email
    email = None
    if not args.cv_only:
        print("✉️  Generating email...", file=sys.stderr)
        email = generate_email(client, job_desc, analysis, contact_name)

    # Generate CV adaptation
    cv = None
    if not args.email_only:
        print("📄 Generating CV adaptation...", file=sys.stderr)
        cv = generate_cv_adaptation(client, job_desc, analysis)

    # Output
    result_lines = []
    import io
    buf = io.StringIO()
    # Capture print output
    old_stdout = sys.stdout
    sys.stdout = buf
    print_result(analysis, email, cv, contact_name)
    sys.stdout = old_stdout
    result = buf.getvalue()

    if args.output:
        Path(args.output).write_text(result, encoding='utf-8')
        print(f"✅ Saved to {args.output}", file=sys.stderr)
    else:
        print(result)


if __name__ == '__main__':
    main()
