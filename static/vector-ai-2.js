/* Vector AI 2.0 — Grok-inspired chat-only sidebar.
   Sidebar options: New chat, Projects, Chats, Settings and Account. */
(function(){
  function loadFinalStyles(){
    if(document.getElementById("vector-ai-grok-final-styles"))return;
    const link=document.createElement("link");link.id="vector-ai-grok-final-styles";link.rel="stylesheet";link.href="/static/vector-ai-2.css?v=2";
    document.body.appendChild(link);
  }
  function installSidebar(){
    const view=document.getElementById("chatView"),sidebar=document.getElementById("chatSidebar");
    if(!view||!sidebar)return;
    loadFinalStyles();
    const brand=sidebar.querySelector(".workspace-brand");
    if(brand)brand.textContent="Vector AI";
    sidebar.querySelectorAll(".workspace-mobile-tools,.chat-toolbar-actions button:not(#newChatButton),#createMediaButton,.workspace-profile").forEach(node=>node.remove());
    sidebar.querySelectorAll(".workspace-section-title").forEach(node=>{
      const label=(node.textContent||"").trim().toLowerCase();
      if(label==="more"||(label==="chats"&&node.nextElementSibling?.id!=="sidebarChats"))node.remove();
    });
    const newChat=document.getElementById("newChatButton"),search=sidebar.querySelector("#chatSidebarSearch");
    if(newChat&&newChat.parentElement!==sidebar)sidebar.insertBefore(newChat,search||null);
    if(search){search.placeholder="Search chats";search.setAttribute("aria-label","Search chats");}
    let footer=document.getElementById("vaAccountSettings");
    if(!footer){
      footer=document.createElement("div");footer.id="vaAccountSettings";
      footer.innerHTML='<button type="button" data-va-nav="settings"><i class="fa-solid fa-gear"></i><span>Settings</span></button><button type="button" data-va-nav="profile"><i class="fa-regular fa-user"></i><span>Account</span></button>';
      sidebar.appendChild(footer);
      footer.querySelectorAll("[data-va-nav]").forEach(button=>button.addEventListener("click",()=>{
        if(typeof window.navigateTo==="function")window.navigateTo(button.dataset.vaNav);
        view.classList.remove("va-chat-sidebar-open");
      }));
    }
    const header=view.querySelector(":scope > .header");
    if(header&&!document.getElementById("vaSidebarToggle")){
      const toggle=document.createElement("button");toggle.id="vaSidebarToggle";toggle.type="button";toggle.title="Open sidebar";toggle.setAttribute("aria-label","Open sidebar");toggle.innerHTML='<i class="fa-solid fa-bars"></i>';
      toggle.addEventListener("click",()=>view.classList.toggle("va-chat-sidebar-open"));header.prepend(toggle);
    }
  }
  function boot(){
    installSidebar();
    const view=document.getElementById("chatView");
    if(view&&!view.dataset.vaSidebarObserver){
      view.dataset.vaSidebarObserver="1";
      new MutationObserver(()=>installSidebar()).observe(view,{childList:true,subtree:true});
    }
  }
  if(document.readyState==="loading")document.addEventListener("DOMContentLoaded",boot,{once:true});else boot();
  window.addEventListener("load",boot,{once:true});
})();
