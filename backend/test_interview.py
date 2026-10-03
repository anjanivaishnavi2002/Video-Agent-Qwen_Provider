"""
Talk to the interviewer in the terminal (text in, text out) - needs GEMINI_API_KEY in backend/.env
(or LLM_PROVIDER=vertex with Google Cloud credentials).

    python test_interview.py "Candidate Name" path/to/resume.txt

Uses exactly the same engine as the web app (resume analysis + dynamic interviewer).
"""
import sys


def main() -> None:
    from app.providers.gemini_provider import get_llm
    from app.services.interview_service import InterviewSession, InterviewSettings
    from app.services.resume_service import analyze_resume, resume_text_for_prompt

    if len(sys.argv) < 3:
        raise SystemExit("Usage: python test_interview.py \"Candidate Name\" path/to/resume.txt")

    name, resume_path = sys.argv[1], sys.argv[2]
    with open(resume_path, encoding="utf-8", errors="ignore") as resume_file:
        resume_text = resume_file.read()

    profile = analyze_resume(resume_text, get_llm())
    print("\n--- extracted profile ---\n", profile)

    session = InterviewSession(
        InterviewSettings.from_config(), name, profile, resume_text_for_prompt(resume_text)
    )
    print("\nAI:", session.start())

    while not session.finished:
        print("\nAI:", session.answer(input("\nYou: ")))

    print("\n--- Interview finished:", session.end_reason, "---")


if __name__ == "__main__":
    main()
