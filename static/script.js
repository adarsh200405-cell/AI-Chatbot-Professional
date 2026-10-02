const $=s=>document.querySelector(s);
const chatBox=$("#chat-box"), input=$("#message-input"), sendBtn=$("#send-btn"), typing=$("#typing");
let currentChatId=null, controller=null, lastUserMessage="", streaming=false;

const settings=JSON.parse(localStorage.getItem("ai_settings")||"{}");
function saveSettings(){localStorage.setItem("ai_settings",JSON.stringify(settings));}
function applySettings(){
  const theme=settings.theme||"system";
  document.body.classList.toggle("dark",theme==="dark" || (theme==="system" && matchMedia("(prefers-color-scheme: dark)").matches));
  document.body.classList.toggle("font-small",settings.font==="small");
  document.body.classList.toggle("font-large",settings.font==="large");
  $("#theme-select").value=theme; $("#language-select").value=settings.language||"en"; $("#font-select").value=settings.font||"medium"; $("#style-select").value=settings.style||"balanced";
}
applySettings();

function escapeHtml(s){const d=document.createElement("div");d.textContent=s;return d.innerHTML}
function renderMarkdown(text){
  if(window.marked) return marked.parse(text,{breaks:true,gfm:true});
  return "<p>"+escapeHtml(text).replace(/\n/g,"<br>")+"</p>";
}
function addCodeButtons(container){
  container.querySelectorAll("pre").forEach(pre=>{
    const b=document.createElement("button");b.className="code-copy";b.textContent="Copy code";
    b.onclick=async()=>{await navigator.clipboard.writeText(pre.querySelector("code")?.innerText||"");b.textContent="Copied!";setTimeout(()=>b.textContent="Copy code",900)};
    pre.appendChild(b);
  });
}
function addMessage(text,role,actions=true){
  $("#welcome")?.remove();
  const row=document.createElement("div");row.className="message "+role;
  const av=document.createElement("div");av.className="avatar";av.textContent=role==="user"?"U":"✦";
  const wrap=document.createElement("div");wrap.className="bubble";
  if(role==="assistant"){wrap.innerHTML=renderMarkdown(text);addCodeButtons(wrap)}
  else wrap.textContent=text;
  row.append(av,wrap);
  if(role==="assistant" && actions){
    const act=document.createElement("div");act.className="msg-actions";
    const copy=document.createElement("button");copy.textContent="Copy";copy.onclick=()=>navigator.clipboard.writeText(text);
    const speakBtn=document.createElement("button");speakBtn.textContent="🔊 Speak";speakBtn.onclick=()=>speak(text);
    const regen=document.createElement("button");regen.textContent="Regenerate";regen.onclick=()=>regenerate();
    act.append(copy,speakBtn,regen);wrap.append(act);
  }
  chatBox.append(row);chatBox.scrollTop=chatBox.scrollHeight;
  return wrap;
}
function showWelcome(){chatBox.innerHTML=`<div id="welcome" class="welcome"><div class="welcome-mark">✦</div><h1>How can I help you today?</h1><p>Ask questions, write code, explain concepts, or upload a PDF to chat with it.</p><div class="suggestions"><button data-prompt="Explain this concept in simple Hinglish.">💡 Explain a concept</button><button data-prompt="Help me write a clean Python program.">⌘ Write code</button><button data-prompt="Give me a step-by-step study plan.">📚 Make a study plan</button></div></div>`}
async function loadChats(q=""){
  const r=await fetch("/api/chats"+(q?"?q="+encodeURIComponent(q):""));const data=await r.json();
  $("#chat-list").innerHTML="";
  data.chats.forEach(c=>{
    const item=document.createElement("div");item.className="chat-item"+(c.id===currentChatId?" active":"");
    item.innerHTML=`<span>▱</span><span class="chat-name"></span><button class="chat-more" title="Rename">⋯</button>`;
    item.querySelector(".chat-name").textContent=c.title;
    item.onclick=(e)=>{if(e.target.classList.contains("chat-more"))return;openChat(c.id)};
    item.querySelector(".chat-more").onclick=(e)=>{e.stopPropagation();renameChat(c.id,c.title)};
    $("#chat-list").append(item);
  });
}
async function createChat(){
  const r=await fetch("/api/chats",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({title:"New Chat"})});
  const c=await r.json();currentChatId=c.id;$("#chat-title").textContent="New Chat";showWelcome();await loadChats();input.focus();closeSidebar();
}
async function openChat(id){
  const r=await fetch("/api/chats/"+id);if(!r.ok)return;
  const data=await r.json();currentChatId=id;$("#chat-title").textContent=data.chat.title;chatBox.innerHTML="";
  if(!data.messages.length)showWelcome();
  data.messages.forEach(m=>addMessage(m.content,m.role==="user"?"user":"assistant",m.role==="assistant"));
  if(data.documents?.length)showAttachment(data.documents[0].filename);
  await loadChats();closeSidebar();
}
async function ensureChat(){if(!currentChatId)await createChat();return currentChatId}
function setBusy(v){streaming=v;sendBtn.disabled=v;typing.classList.toggle("hidden",!v);if(!v)$("#stop-btn").disabled=false}
async function sendMessage(){
  const message=input.value.trim();if(!message||streaming)return;
  await ensureChat();lastUserMessage=message;input.value="";resizeInput();addMessage(message,"user");setBusy(true);
  const bubble=addMessage("", "assistant", false);bubble.innerHTML='<span class="stream-cursor">▍</span>';
  controller=new AbortController();
  try{
    const r=await fetch("/api/chat",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({conversation_id:currentChatId,message}),signal:controller.signal});
    if(!r.ok){const e=await r.json();throw new Error(e.error||"Request failed")}
    await readStream(r,bubble);
  }catch(e){if(e.name!=="AbortError"){bubble.innerHTML="<p>"+escapeHtml(e.message)+"</p>"}}
  finally{controller=null;setBusy(false);await loadChats();input.focus()}
}
async function readStream(response,bubble){
  const reader=response.body.getReader(),decoder=new TextDecoder();let buffer="",full="";
  while(true){const {value,done}=await reader.read();if(done)break;buffer+=decoder.decode(value,{stream:true});const parts=buffer.split("\n\n");buffer=parts.pop();
    for(const block of parts){const line=block.split("\n").find(x=>x.startsWith("data: "));if(!line)continue;let d;try{d=JSON.parse(line.slice(6))}catch{continue}
      if(d.type==="chunk"){full+=d.text;bubble.innerHTML=renderMarkdown(full);addCodeButtons(bubble);chatBox.scrollTop=chatBox.scrollHeight}
      if(d.type==="error"){bubble.innerHTML="<p>"+escapeHtml(d.message)+"</p>"}
    }
  }
}
function stopGeneration(){if(controller)controller.abort();setBusy(false)}
async function regenerate(){
  if(!currentChatId||streaming)return;
  const old=[...chatBox.querySelectorAll(".message.assistant")].pop();old?.remove();setBusy(true);
  const bubble=addMessage("","assistant",false);controller=new AbortController();
  try{const r=await fetch("/api/regenerate/"+currentChatId,{method:"POST",signal:controller.signal});await readStream(r,bubble)}catch(e){if(e.name!=="AbortError")bubble.innerHTML="<p>"+escapeHtml(e.message)+"</p>"}finally{controller=null;setBusy(false);await loadChats()}
}
async function renameChat(id,title){const next=prompt("Chat name:",title);if(next===null)return;await fetch("/api/chats/"+id,{method:"PATCH",headers:{"Content-Type":"application/json"},body:JSON.stringify({title:next})});if(id===currentChatId)$("#chat-title").textContent=next;loadChats()}
async function deleteCurrent(){
  if(!currentChatId)return;
  if(!confirm("Clear this chat?"))return;
  await fetch("/api/chats/"+currentChatId,{method:"DELETE"});currentChatId=null;showWelcome();$("#chat-title").textContent="New Chat";loadChats();
}
async function clearAll(){if(confirm("Delete ALL chat history? This cannot be undone.")){await fetch("/api/chats",{method:"DELETE"});currentChatId=null;showWelcome();$("#chat-title").textContent="New Chat";loadChats();closeModals()}}
function resizeInput(){input.style.height="auto";input.style.height=Math.min(input.scrollHeight,180)+"px"}
function showAttachment(name){$("#attachment").classList.remove("hidden");$("#attachment").innerHTML=`<span>📎 ${escapeHtml(name)}</span>`}
async function uploadPdf(file){
  await ensureChat();const fd=new FormData();fd.append("file",file);fd.append("conversation_id",currentChatId);$("#attachment").classList.remove("hidden");$("#attachment").textContent="Uploading PDF…";
  const r=await fetch("/api/upload-pdf",{method:"POST",body:fd});const d=await r.json();if(!r.ok){alert(d.error||"Upload failed");$("#attachment").classList.add("hidden");return}showAttachment(d.filename);input.value=`I uploaded ${d.filename}. `;
}
function closeModals(){document.querySelectorAll(".modal").forEach(m=>m.classList.add("hidden"))}
function closeSidebar(){$("#sidebar").classList.remove("open")}
async function voiceInput(){
  const SR=window.SpeechRecognition||window.webkitSpeechRecognition;if(!SR){alert("Speech recognition is not supported in this browser. Try Chrome.");return}
  const rec=new SR();rec.lang=settings.language==="hinglish"?"en-IN":"en-US";rec.interimResults=false;$("#voice-status").textContent="Listening…";rec.onresult=e=>{input.value+=(input.value?" ":"")+e.results[0][0].transcript;resizeInput()};rec.onerror=()=>$("#voice-status").textContent="";rec.onend=()=>$("#voice-status").textContent="";rec.start()
}
function speak(text){if(!("speechSynthesis" in window)){alert("Text-to-speech is not supported.");return}speechSynthesis.cancel();speechSynthesis.speak(new SpeechSynthesisUtterance(text))}
function closeOnOutside(e){document.querySelectorAll(".modal").forEach(m=>{if(e.target===m)m.classList.add("hidden")})}

$("#new-chat").onclick=createChat;$("#send-btn").onclick=sendMessage;$("#stop-btn").onclick=stopGeneration;$("#clear-btn").onclick=deleteCurrent;$("#voice-btn").onclick=voiceInput;$("#attach-btn").onclick=()=>$("#file-input").click();
$("#file-input").onchange=e=>e.target.files[0]&&uploadPdf(e.target.files[0]);
input.addEventListener("input",resizeInput);input.addEventListener("keydown",e=>{if(e.key==="Enter"&&!e.shiftKey){e.preventDefault();sendMessage()}});
$("#search-toggle").onclick=()=>$("#search-wrap").classList.toggle("hidden");$("#history-search").oninput=e=>loadChats(e.target.value);
$("#menu-btn").onclick=()=>$("#sidebar").classList.toggle("open");
$("#theme-btn").onclick=()=>{settings.theme=document.body.classList.contains("dark")?"light":"dark";saveSettings();applySettings()};
$("#settings-btn").onclick=()=>$("#settings-modal").classList.remove("hidden");$("#about-btn").onclick=()=>$("#about-modal").classList.remove("hidden");
document.querySelectorAll(".close-modal").forEach(b=>b.onclick=closeModals);document.querySelectorAll(".modal").forEach(m=>m.onclick=closeOnOutside);
$("#clear-all").onclick=clearAll;
$("#theme-select").onchange=e=>{settings.theme=e.target.value;saveSettings();applySettings()};$("#language-select").onchange=e=>{settings.language=e.target.value;saveSettings()};
$("#font-select").onchange=e=>{settings.font=e.target.value;saveSettings();applySettings()};$("#style-select").onchange=e=>{settings.style=e.target.value;saveSettings()};
document.addEventListener("click",e=>{const b=e.target.closest("[data-prompt]");if(b){input.value=b.dataset.prompt;resizeInput();input.focus()}});
window.addEventListener("beforeunload",()=>controller?.abort());
document.addEventListener("DOMContentLoaded",async()=>{await loadChats();const r=await fetch("/api/chats");const d=await r.json();if(d.chats.length)await openChat(d.chats[0].id);else await createChat()});
