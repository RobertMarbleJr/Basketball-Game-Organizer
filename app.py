"""Simple basketball game-day scheduling website.
Run: pip install -r requirements.txt && streamlit run app.py
Set OPENAI_API_KEY in environment to enable AI requests.
"""
import os, json, uuid, csv, tempfile
from datetime import datetime, timedelta
import streamlit as st
import io

LIBRARY = ["National Anthem", "Team Entrance", "Starting Lineups", "Sponsor Recognition",
           "Media Timeout", "Timeout Promotion", "Halftime Performance", "Postgame Recognition"]
PERIODS = ["Pregame", "First Quarter", "Second Quarter", "Halftime",
           "Third Quarter", "Fourth Quarter", "Overtime", "Postgame"]
STAFF = {"National Anthem":"PA Announcer, Audio Operator",
         "Team Entrance":"PA Announcer, DJ",
         "Starting Lineups":"PA Announcer, DJ",
         "Sponsor Recognition":"PA Announcer, Video Operator",
         "Media Timeout":"Game Operations Director",
         "Timeout Promotion":"PA Announcer, DJ",
         "Halftime Performance":"Game Operations Director, DJ",
         "Postgame Recognition":"PA Announcer"}
def parse_cue(period, cue):
    cue = cue.strip()
    if period in ["First Quarter", "Second Quarter", "Third Quarter", "Fourth Quarter", "Overtime"]:
        a,b = map(int, cue.split(":"))
        limit = 5 if period=="Overtime" else 10
        if not (0<=a<=limit and 0<=b<60 and (a<limit or b==0)):
            raise ValueError(f"Use a game clock from {limit:02d}:00 to 00:00 remaining")
        return f"{a:02d}:{b:02d}"
    return datetime.strptime(cue.upper(), "%I:%M %p").strftime("%I:%M %p")
def rows(items):
    def key(e):
        period = PERIODS.index(e["period"])
        if e["period"] in PERIODS[1:3]+PERIODS[4:7]:
            a,b=map(int,e["cue"].split(":"))
            return (period,-(a*60+b))
        return (period,datetime.strptime(e["cue"], "%I:%M %p").time())
    return [[e["id"],e["period"],e["cue"],e["title"],e["staff"]] for e in sorted(items,key=key)]
def add(items, title, period, cue):
    items=list(items or [])
    if title not in LIBRARY: return items,rows(items),"Unknown event."
    try: cue=parse_cue(period,cue)
    except Exception as exc: return items,rows(items),f"Invalid time: {exc}"
    item={"id":uuid.uuid4().hex[:6],"title":title,"period":period,"cue":cue,"staff":STAFF[title]}
    items.append(item)
    return items,rows(items),f"Added {title} ({period}, {cue})."
def delete(items, event_id):
    items=list(items or [])
    new=[e for e in items if e["id"]!=event_id.strip()]
    return new,rows(new),"Removed." if len(new)!=len(items) else "ID not found."
def export(items, home, away, tipoff):
    try: tipoff=datetime.strptime(tipoff.strip().upper(),"%I:%M %p").strftime("%I:%M %p")
    except ValueError: return None,"Tipoff should look like 7:00 PM."
    path=os.path.join(tempfile.gettempdir(),"basketball_game_script.csv")
    with open(path,"w",newline="",encoding="utf-8") as f:
        writer=csv.writer(f)
        writer.writerow(["Home",home,"Visitor",away,"Tipoff",tipoff])
        writer.writerow(["ID","Period","Cue","Event","Personnel"])
        writer.writerows(rows(items or []))
    return path,"Export ready."

TOOLS=[{"type":"function","name":"schedule_event",
        "description":"Schedule an event occurrence, including duplicates. For quarters use game-clock MM:SS remaining, not wall-clock time.",
        "strict":True,"parameters":{"type":"object","properties":{
            "title":{"type":"string","enum":LIBRARY},
            "period":{"type":"string","enum":PERIODS},
            "cue":{"type":"string","description":"Quarter: MM:SS remaining; pregame/halftime/postgame: h:mm AM/PM"}},
            "required":["title","period","cue"],"additionalProperties":False}},
       {"type":"function","name":"remove_event",
        "description":"Remove an existing event by its exact ID.",
        "strict":True,"parameters":{"type":"object","properties":{"event_id":{"type":"string"}},
        "required":["event_id"],"additionalProperties":False}},
       {"type":"function","name":"change_event",
        "description":"Change the period or cue for an existing event by exact ID.",
        "strict":True,"parameters":{"type":"object","properties":{
            "event_id":{"type":"string"},"period":{"type":"string","enum":PERIODS},
            "cue":{"type":"string"}},"required":["event_id","period","cue"],"additionalProperties":False}}]
def ai_request(items, request, tipoff):
    items=list(items or [])
    if not request.strip(): return items,rows(items),"Enter a request."
    key=os.getenv("OPENAI_API_KEY")
    if not key: return items,rows(items),"Scheduling service is not connected. Configure OPENAI_API_KEY before starting the application."
    try:
        from openai import OpenAI
        client=OpenAI(api_key=key)
        history=[{"role":"user","content":json.dumps({"tipoff":tipoff,"events":items,"request":request})}]
        notes=[]
        for _ in range(5):
            result=client.responses.create(model="gpt-4.1-mini",tools=TOOLS,
                instructions=("You are a basketball game operations scheduling assistant. "
                 "Basketball quarters have 10:00 game-clock time remaining at their start. "
                 "For '5 minutes into first quarter' use First Quarter, 05:00 remaining. "
                 "Never infer actual wall-clock time from game clock. "
                 "For halftime, postgame or pregame ask for explicit real time unless given. "
                 "Use tool calls to change the schedule; never claim success without tool result. "
                 "For changes/removals of duplicate events, require an ID. "
                 "Do not invent unsupported event types."),
                input=history)
            history.extend(result.output)
            calls=[x for x in result.output if x.type=="function_call"]
            if not calls:
                if result.output_text: notes.append(result.output_text)
                break
            for call in calls:
                args=json.loads(call.arguments)
                if call.name=="schedule_event":
                    items,_,message=add(items,args["title"],args["period"],args["cue"])
                elif call.name=="remove_event":
                    items,_,message=delete(items,args["event_id"])
                elif call.name=="change_event":
                    match=next((x for x in items if x["id"]==args["event_id"]),None)
                    if match:
                        try:
                            cue=parse_cue(args["period"],args["cue"])
                            match.update(period=args["period"],cue=cue)
                            message="Updated event "+match["id"]
                        except Exception as exc: message="Error: "+str(exc)
                    else: message="Event ID not found."
                else: message="Unknown tool."
                notes.append(message)
                history.append({"type":"function_call_output","call_id":call.call_id,
                                "output":json.dumps({"result":message})})
        return items,rows(items),"\n".join(notes) or "No change."
    except Exception as exc:
        return items,rows(items),"Could not process your request. Check the API configuration and billing."


st.set_page_config(page_title="Basketball Game Day Scheduler", page_icon="🏀", layout="wide")
st.title("🏀 Basketball Game Day Scheduler")
st.write("Set your tipoff time, then describe the changes you want to make.")
if "items" not in st.session_state:
    st.session_state["items"] = []
if "status" not in st.session_state:
    st.session_state.status = ""
a,b,c = st.columns(3)
home = a.text_input("Home team", "Home")
away = b.text_input("Visiting team", "Visitors")
tipoff = c.text_input("Tipoff time", "7:00 PM")
with st.expander("Available events, game periods and example instructions", expanded=True):
    st.write("**Available events:** " + " · ".join(LIBRARY))
    st.write("**Game periods:** " + " · ".join(PERIODS))
    st.markdown("""- Schedule the National Anthem at 6:50 PM during pregame.
- Add Sponsor Recognition with 05:00 remaining in the first quarter.
- Move event ID abc123 to 04:00 remaining in the second quarter.
- Remove event ID abc123.

For quarters, use game-clock time remaining. For pregame, halftime and postgame, use real time.""")
with st.form("instructions"):
    request = st.text_area("Your instructions", height=150)
    submitted = st.form_submit_button("Update Game Script", type="primary")
if submitted:
    with st.spinner("Updating game script..."):
        st.session_state["items"], _, st.session_state.status = ai_request(st.session_state["items"], request, tipoff)
if st.session_state.status:
    st.text(st.session_state.status)
st.subheader("Game Script")
headers = ["ID", "Period", "Time / Game clock", "Event", "Personnel"]
records = [dict(zip(headers, row)) for row in rows(st.session_state["items"])]
if records:
    st.dataframe(records, hide_index=True, use_container_width=True)
else:
    st.info("Your game script is empty. Enter an instruction to get started.")
try:
    formatted = datetime.strptime(tipoff.strip().upper(), "%I:%M %p").strftime("%I:%M %p")
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Home", home, "Visitor", away, "Tipoff", formatted])
    writer.writerow(["ID", "Period", "Cue", "Event", "Personnel"])
    writer.writerows(rows(st.session_state["items"]))
    st.download_button("Export Game Script (CSV)", buffer.getvalue(), "basketball_game_script.csv", "text/csv")
except ValueError:
    st.warning("Tipoff should look like 7:00 PM to export the script.")
