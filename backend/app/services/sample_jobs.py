"""30 sample BPO jobs (fresher to team-lead) an admin can add with one click. Idempotent: matched by title."""
from sqlalchemy.orm import Session

from app.db.models import Job

# (title, department, location, process, min_years, max_years, description, skills)
SAMPLES = [
    # ---- freshers (0-1 years) -------------------------------------------------------------------------------
    ("Customer Support Executive - Voice (Inbound)", "Customer Support", "Hyderabad", "voice", 0, 1,
     "Take inbound calls for a telecom client, answer billing and plan questions, and log every contact in the CRM. "
     "Full training is given. Rotational shifts.", ["English", "Active listening", "Empathy"]),
    ("Chat Support Associate - Fresher", "Customer Support", "Bengaluru", "chat", 0, 1,
     "Answer customers on live chat for an e-commerce client with clear, polite writing. You will handle two chats at "
     "a time after training.", ["Written English", "Typing speed", "Politeness"]),
    ("Email Support Associate - Fresher", "Customer Support", "Pune", "email", 0, 1,
     "Reply to customer emails for a retail client using approved templates, tagging each ticket correctly and "
     "meeting the daily response target.", ["Written English", "Grammar", "Attention to detail"]),
    ("Data Entry & Back Office Executive", "Back Office", "Chennai", "back_office", 0, 1,
     "Update customer records, verify documents and clear the daily work queue with high accuracy.",
     ["Typing", "MS Excel", "Accuracy"]),
    ("Voice Process Trainee - Hindi & English", "Customer Support", "Noida", "voice", 0, 1,
     "Bilingual outbound and inbound calling for a banking client. Paid training for 4 weeks, then production floor.",
     ["Hindi", "English", "Communication"]),
    ("Customer Care Associate - Blended", "Customer Support", "Kolkata", "blended", 0, 1,
     "Support customers across phone and chat for a travel brand, handling booking changes and refund queries.",
     ["English", "Multitasking", "Patience"]),
    # ---- 1 to 3 years ------------------------------------------------------------------------------------------
    ("Customer Support Executive - Voice (Senior Associate)", "Customer Support", "Hyderabad", "voice", 1, 3,
     "Handle inbound calls for a telecom client, resolve billing and service issues on first contact and meet "
     "quality and handle-time targets.", ["English", "CRM", "Empathy", "De-escalation"]),
    ("Chat Support Specialist", "Customer Support", "Bengaluru", "chat", 1, 3,
     "Handle up to three live chats at once for a fintech client, follow compliance scripts and keep first-response "
     "time low.", ["Written English", "Multitasking", "CRM", "Typing speed"]),
    ("Email Support Specialist", "Customer Support", "Pune", "email", 1, 3,
     "Resolve complex customer emails for a software client, write original replies (not only templates) and escalate "
     "correctly.", ["Written English", "Ticketing tools", "Problem solving"]),
    ("Technical Support Specialist (L1)", "Technical Support", "Pune", "blended", 1, 3,
     "First-line troubleshooting of internet and device issues by voice and chat using knowledge-base articles, "
     "escalating to L2 when needed.", ["Troubleshooting", "English", "Networking basics"]),
    ("Collections Executive - Voice", "Collections", "Chennai", "voice", 1, 3,
     "Call customers about due payments, negotiate repayment plans politely and stay within compliance scripts.",
     ["Negotiation", "Hindi", "English", "Compliance"]),
    ("Order Management Associate - Email & Chat", "Operations", "Gurugram", "blended", 1, 3,
     "Track orders, resolve delivery issues and update customers by email and chat for an e-commerce marketplace.",
     ["Order tracking", "Written English", "CRM"]),
    ("Banking Process Executive - Voice", "Banking & Finance", "Mumbai", "voice", 1, 3,
     "Assist credit-card customers on calls: card activation, disputes, limit queries and fraud alerts with strict "
     "verification steps.", ["Banking basics", "English", "Verification", "Empathy"]),
    ("Quality Analyst - Associate", "Quality", "Hyderabad", "other", 1, 3,
     "Audit recorded calls, chats and emails against the quality form, give feedback to agents and report trends.",
     ["Call auditing", "Feedback", "MS Excel"]),
    # ---- 3 to 5 years ------------------------------------------------------------------------------------------
    ("Senior Customer Support Executive - Voice", "Customer Support", "Hyderabad", "voice", 3, 5,
     "Take escalated calls for a premium telecom segment, coach new joiners on the floor and keep CSAT above target.",
     ["De-escalation", "Coaching", "CRM", "English"]),
    ("Senior Chat Support Specialist", "Customer Support", "Bengaluru", "chat", 3, 5,
     "Handle high-value and escalated chats for a SaaS client, write knowledge-base articles and mentor junior "
     "agents.", ["Written English", "Escalation handling", "Knowledge base", "Mentoring"]),
    ("Senior Email Support Specialist", "Customer Support", "Pune", "email", 3, 5,
     "Own complex and escalated email cases, draft policy-sensitive replies and review the work of junior agents.",
     ["Written English", "Policy interpretation", "Escalations", "Quality review"]),
    ("Technical Support Specialist (L2)", "Technical Support", "Chennai", "blended", 3, 5,
     "Resolve escalated technical issues for broadband and device customers, document root causes and raise "
     "problem tickets.", ["Troubleshooting", "Networking", "Root-cause analysis", "English"]),
    ("Process Trainer - Customer Support", "Training", "Hyderabad", "other", 3, 5,
     "Run induction and refresher training for voice, chat and email agents, track throughput and certify nesting "
     "batches.", ["Training delivery", "Communication", "Content creation"]),
    ("Retention Specialist - Voice", "Retention", "Noida", "voice", 3, 5,
     "Win back customers who want to cancel by understanding the reason, offering the right plan and closing "
     "politely.", ["Negotiation", "Sales", "Empathy", "CRM"]),
    ("Escalation Desk Executive - Email & Chat", "Customer Support", "Bengaluru", "blended", 3, 5,
     "Work the executive-complaints desk: investigate, coordinate with internal teams and reply within SLA.",
     ["Written English", "Investigation", "Stakeholder management"]),
    ("Workforce Management Analyst", "Operations", "Hyderabad", "other", 3, 5,
     "Forecast volumes, build rosters and monitor real-time adherence for a 300-seat voice and chat operation.",
     ["Forecasting", "MS Excel", "Scheduling", "Reporting"]),
    # ---- 5 to 8 years ------------------------------------------------------------------------------------------
    ("Subject Matter Expert - Voice & Chat", "Customer Support", "Pune", "blended", 5, 8,
     "The floor expert for a telecom account: take the hardest calls, support agents on live issues and update "
     "process documents.", ["Process expertise", "Coaching", "Escalations", "English"]),
    ("Team Leader - Customer Support (Voice)", "Operations", "Hyderabad", "voice", 5, 8,
     "Lead 15 voice agents: coach on quality, track SLAs, handle escalations and report daily performance.",
     ["Coaching", "People management", "MIS reporting", "BPO experience"]),
    ("Team Leader - Chat & Email", "Operations", "Bengaluru", "blended", 5, 8,
     "Lead a team of 20 chat and email agents, manage queues, review written quality and drive CSAT.",
     ["People management", "Written quality", "Queue management", "Reporting"]),
    ("Quality Team Lead", "Quality", "Chennai", "other", 5, 8,
     "Lead a team of quality analysts, run calibration sessions with clients and own the quality score for the "
     "account.", ["Calibration", "Quality frameworks", "Coaching", "Client communication"]),
    ("Customer Experience Specialist - Email & Chat", "Customer Experience", "Mumbai", "blended", 5, 8,
     "Design and improve written customer journeys: templates, macros and chatbot hand-offs, and measure the "
     "impact.", ["Written English", "Process design", "Analytics", "CX tools"]),
    # ---- 8+ years ---------------------------------------------------------------------------------------------
    ("Assistant Manager - Customer Operations", "Operations", "Hyderabad", "blended", 8, 12,
     "Manage 4 team leaders and about 80 agents across voice, chat and email: delivery against SLA, attrition and "
     "client reviews.", ["People management", "P&L basics", "Client management", "Operations"]),
    ("Operations Manager - BPO Account", "Operations", "Gurugram", "blended", 8, 15,
     "Own an entire account of 250+ seats: client relationship, financials, hiring plan and transformation "
     "projects.", ["Account management", "Budgeting", "Leadership", "Transformation"]),
    ("Head of Training & Quality", "Training", "Bengaluru", "other", 10, 20,
     "Lead the training and quality function for several accounts, set the curriculum and own certification and "
     "audit standards.", ["Leadership", "Curriculum design", "Quality frameworks", "Stakeholder management"]),
]


def add_sample_jobs(db: Session) -> int:
    """Create the sample jobs that do not exist yet (matched by title); fill the new fields on older copies."""
    have = {j.title: j for j in db.query(Job).all()}
    added = 0
    for title, dept, loc, process, low, high, desc, skills in SAMPLES:
        old = have.get(title)
        if old:
            if old.process_type is None and old.experience_min is None and old.experience_max is None:
                old.process_type, old.experience_min, old.experience_max = process, low, high
            continue
        db.add(Job(title=title, department=dept, location=loc, employment_type="full_time", process_type=process,
                   experience_min=low, experience_max=high, description=desc, required_skills=skills, status="open"))
        added += 1
    db.commit()
    return added
