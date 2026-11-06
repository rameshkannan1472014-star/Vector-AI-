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
    header.innerHTML='<div class="nexus-topbrand"><div class="nexus-small-mark"><i class="fa-solid fa-gear"></i></div><span>Nexus AI</span><span class="nexus-divider"></span><strong>Grok</strong></div>';
    const menu=document.createElement("button");menu.type="button";menu.className="nexus-menu";menu.setAttribute("aria-label","Toggle sidebar");menu.innerHTML='<i class="fa-solid fa-bars"></i>';
    header.appendChild(menu);main.prepend(header);
    menu.setAttribute("aria-expanded","false");
    function setSidebarOpen(open){
      view.classList.toggle("nexus-sidebar-open",open);
      menu.setAttribute("aria-expanded",String(open));
      if(window.matchMedia("(max-width:760px)").matches){
        sidebar.style.setProperty("position","fixed","important");
        sidebar.style.setProperty("inset","0 auto 0 0","important");
        sidebar.style.setProperty("width","min(82vw,300px)","important");
        sidebar.style.setProperty("height","100dvh","important");
        sidebar.style.setProperty("z-index","1202","important");
        sidebar.style.setProperty("transform",open?"translateX(0)":"translateX(-105%)","important");
        sidebar.style.setProperty("display","flex","important");
        backdrop.style.display=open?"block":"none";
      }
    }
    menu.addEventListener("click",()=>setSidebarOpen(!view.classList.contains("nexus-sidebar-open")));
    if(welcome)welcome.remove();
    if(messages)main.appendChild(messages);
    if(loading)main.appendChild(loading);
    if(attachments)main.appendChild(attachments);
    if(composer){composer.classList.add("nexus-composer");main.appendChild(composer);}
    if(search)search.addEventListener("input",()=>{});
    const active=()=>{nav.querySelectorAll(".nexus-nav-item").forEach(b=>b.classList.toggle("active",b.textContent.trim()==="Chat"&&$("chatView")?.classList.contains("active")));};
    window.addEventListener("hashchange",active);active();
    if(!location.hash||location.hash==="#home")go("chat");
    const backdrop=document.createElement("div");backdrop.className="nexus-backdrop";backdrop.addEventListener("click",()=>setSidebarOpen(false));view.appendChild(backdrop);
    sidebar.querySelectorAll(".nexus-nav-item").forEach(button=>button.addEventListener("click",()=>setSidebarOpen(false)));
    window.addEventListener("resize",()=>{if(!window.matchMedia("(max-width:760px)").matches){sidebar.style.removeProperty("position");sidebar.style.removeProperty("inset");sidebar.style.removeProperty("width");sidebar.style.removeProperty("height");sidebar.style.removeProperty("z-index");sidebar.style.removeProperty("transform");backdrop.style.display="none";view.classList.remove("nexus-sidebar-open");}});
  }
  if(document.readyState==="loading")document.addEventListener("DOMContentLoaded",setup,{once:true});else setup();
  window.addEventListener("load",setup,{once:true});

  // The chat workspace is created lazily by the app's navigation code.
  // Re-run setup immediately after that DOM is created; load events alone
  // are too early on some mobile sessions.
  function hookWorkspaceBuilder(){
    const builder=window.buildChatWorkspace;
    if(typeof builder==="function" && !builder.__vectorAiSidebarHooked){
      const wrapped=function(){
        const result=builder.apply(this,arguments);
        setup();
        return result;
      };
      wrapped.__vectorAiSidebarHooked=true;
      window.buildChatWorkspace=wrapped;
    }
  }
  hookWorkspaceBuilder();
  // Some app flows create the workspace through installChatToolbar.
  const installToolbar=window.installChatToolbar;
  if(typeof installToolbar==="function" && !installToolbar.__vectorAiSidebarHooked){
    const wrappedInstall=function(){
      const result=installToolbar.apply(this,arguments);
      hookWorkspaceBuilder();
      setup();
      return result;
    };
    wrappedInstall.__vectorAiSidebarHooked=true;
    window.installChatToolbar=wrappedInstall;
  }


  // Delegated mobile sidebar control: survives lazy DOM creation and
  // replacement of the menu/sidebar nodes by the legacy workspace builder.
  if(!window.__vectorAiSidebarDelegation){
    window.__vectorAiSidebarDelegation=true;
    function setOpen(open){
      const view=document.getElementById("chatView");
      const sidebar=document.getElementById("chatSidebar");
      const menu=document.querySelector(".nexus-menu, #chatMobileMenu");
      const backdrop=view?.querySelector(".nexus-backdrop")||document.getElementById("chatSidebarBackdrop");
      if(!view||!sidebar)return;
      view.classList.toggle("nexus-sidebar-open",open);
      view.classList.toggle("vector-sidebar-open",open);
      menu?.setAttribute("aria-expanded",String(open));
      if(window.innerWidth<=760){
        Object.assign(sidebar.style,{
          position:"fixed",top:"0",left:"0",bottom:"0",right:"auto",
          width:"min(82vw,300px)",height:"100dvh",maxHeight:"100dvh",
          zIndex:"10002",display:"flex",transform:open?"translateX(0)":"translateX(-105%)"
        });
        if(backdrop){
          Object.assign(backdrop.style,{
            display:open?"block":"none",position:"fixed",inset:"0",
            zIndex:"10001",background:"rgba(0,0,0,.62)",pointerEvents:open?"auto":"none"
          });
        }
      }
    }
    document.addEventListener("click",function(event){
      const trigger=event.target.closest(".nexus-menu, #chatMobileMenu");
      if(trigger){
        event.preventDefault();event.stopPropagation();
        const view=document.getElementById("chatView");
        setOpen(!view?.classList.contains("vector-sidebar-open"));
        return;
      }
      if(event.target.closest(".nexus-backdrop, #chatSidebarBackdrop")){
        setOpen(false);return;
      }
      if(window.innerWidth<=760 && event.target.closest("#chatSidebar a, #chatSidebar button")){
        setOpen(false);
      }
    },true);
  }

})();