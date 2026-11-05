/* Nexus AI — Grok-inspired chat workspace */
(function(){
  const $=id=>document.getElementById(id);
  const navItems=[
    {label:"Chat",icon:"fa-regular fa-message",view:"chat"},
    {label:"Projects",icon:"fa-regular fa-folder",view:"projects"},
    {label:"Settings",icon:"fa-solid fa-gear",view:"settings"},
    {label:"About",icon:"fa-regular fa-circle-info",view:"profile"},
    {label:"Help",icon:"fa-regular fa-circle-question",view:"feedback"}
  ];
  function makeButton(label,icon,action,cls=""){
    const b=document.createElement("button");b.type="button";b.className="nexus-nav-item "+cls;
    b.innerHTML='<i class="'+icon+'"></i><span>'+label+'</span>';b.addEventListener("click",action);return b;
  }
  function go(view){if(typeof window.navigateTo==="function")window.navigateTo(view);}
  function setup(){
    const view=$("chatView"),layout=$("chatAppLayout"),sidebar=$("chatSidebar"),main=$("chatMain");
    if(!view||!layout||!sidebar||!main)return;
    if(view.dataset.nexusUiReady==="1")return;
    view.dataset.nexusUiReady="1";
    const projects=$("sidebarProjects"),chats=$("sidebarChats"),search=$("chatSidebarSearch");
    const newChat=$("newChatButton"),welcome=$("chatWelcome"),messages=$("chatMessages");
    const composer=view.querySelector(".composer");
    const status=$("chatStatus"),loading=$("chatLoading"),attachments=$("chatAttachments");
    sidebar.replaceChildren();
    const brand=document.createElement("div");brand.className="nexus-brand";
    brand.innerHTML='<div class="nexus-mark"><i class="fa-solid fa-gear"></i><span class="nexus-orbit"></span></div><div>Nexus <b>AI</b></div>';
    sidebar.appendChild(brand);
    const nav=document.createElement("nav");nav.className="nexus-sidebar-nav";
    navItems.forEach(item=>nav.appendChild(makeButton(item.label,item.icon,()=>go(item.view),item.view==="chat"?"active":"")));
    sidebar.appendChild(nav);
    const projectHead=document.createElement("div");projectHead.className="nexus-section-label";projectHead.textContent="Projects";sidebar.appendChild(projectHead);
    if(projects){projects.classList.add("nexus-list");sidebar.appendChild(projects);}
    const chatHead=document.createElement("div");chatHead.className="nexus-section-label";chatHead.textContent="Recent chats";sidebar.appendChild(chatHead);
    if(search){search.placeholder="Search chats";search.className="nexus-search";sidebar.appendChild(search);}
    if(chats){chats.classList.add("nexus-list");sidebar.appendChild(chats);}
    const footer=document.createElement("div");footer.className="nexus-sidebar-footer";
    footer.appendChild(makeButton("Account","fa-regular fa-user",()=>go("profile")));
    sidebar.appendChild(footer);
    if(newChat){newChat.className="nexus-new-chat";newChat.innerHTML='<i class="fa-solid fa-plus"></i><span>New chat</span>';sidebar.insertBefore(newChat,nav);}
    layout.querySelectorAll("#chatNavRail,#chatToolsRail").forEach(n=>n.remove());
    const oldConversation=$("chatConversationBar");if(oldConversation)oldConversation.remove();
    if(status)status.remove();
    const header=document.createElement("div");header.className="nexus-topbar";
    header.innerHTML='<div class="nexus-topbrand"><div class="nexus-small-mark"><i class="fa-solid fa-gear"></i></div><span>Nexus AI</span><span class="nexus-divider"></span><strong>Grok-style workspace</strong></div>';
    const menu=document.createElement("button");menu.type="button";menu.className="nexus-menu";menu.setAttribute("aria-label","Toggle sidebar");menu.innerHTML='<i class="fa-solid fa-bars"></i>';
    header.appendChild(menu);main.prepend(header);
    menu.addEventListener("click",()=>view.classList.toggle("nexus-sidebar-open"));
    const welcomePanel=document.createElement("section");welcomePanel.className="nexus-welcome";
    welcomePanel.innerHTML='<h1>Hello! <span class="nexus-wave">👋</span></h1><p>I’m Nexus AI, your personal assistant. How can I help you today?</p><div class="nexus-suggestions"></div>';
    const suggestions=welcomePanel.querySelector(".nexus-suggestions");
    [
      ["fa-solid fa-wand-magic-sparkles","Explain anything","Explain this topic clearly"],
      ["fa-solid fa-code","Write code","Help me write code"],
      ["fa-solid fa-bolt","Help with projects","Help me with my project"],
      ["fa-regular fa-lightbulb","Get ideas","Give me creative ideas"]
    ].forEach(([icon,label,prompt])=>suggestions.appendChild(makeButton(label,icon,()=>{if(typeof window.useChatPrompt==="function")window.useChatPrompt(prompt);else if($("chatInput")){$("chatInput").value=prompt;$("chatInput").focus();}},"nexus-suggestion")));
    if(welcome)welcome.remove();
    if(messages){main.appendChild(welcomePanel);main.appendChild(messages);}
    if(loading)main.appendChild(loading);
    if(attachments)main.appendChild(attachments);
    if(composer){composer.classList.add("nexus-composer");main.appendChild(composer);}
    if(search)search.addEventListener("input",()=>{});
    const active=()=>{nav.querySelectorAll(".nexus-nav-item").forEach(b=>b.classList.toggle("active",b.textContent.trim()==="Chat"&&$("chatView")?.classList.contains("active")));};
    window.addEventListener("hashchange",active);active();
    const backdrop=document.createElement("div");backdrop.className="nexus-backdrop";backdrop.addEventListener("click",()=>view.classList.remove("nexus-sidebar-open"));view.appendChild(backdrop);
  }
  if(document.readyState==="loading")document.addEventListener("DOMContentLoaded",setup,{once:true});else setup();
  window.addEventListener("load",setup,{once:true});
})();