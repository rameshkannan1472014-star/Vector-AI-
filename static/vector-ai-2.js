/* Vector AI 2.0 workspace controls. Auth and cloud sync are added in a later backend phase. */
(function(){
  function installVectorWorkspace(){
    const view=document.getElementById("chatView");
    const sidebar=document.getElementById("chatSidebar");
    const header=view?.querySelector(":scope > .header");
    if(!view||!sidebar||!header||document.getElementById("vaAccountSettings"))return;
    const toggle=document.createElement("button");
    toggle.id="vaSidebarToggle";toggle.type="button";toggle.title="Chat history";toggle.setAttribute("aria-label","Open chat history");
    toggle.innerHTML='<i class="fa-solid fa-bars"></i>';
    toggle.addEventListener("click",()=>view.classList.toggle("va-chat-sidebar-open"));
    header.prepend(toggle);
    const footer=document.createElement("div");footer.id="vaAccountSettings";
    footer.innerHTML='<div class="va-account-caption">YOUR WORKSPACE</div><button type="button" data-va-nav="profile"><i class="fa-regular fa-user"></i><span>Account details</span></button><button type="button" data-va-nav="settings"><i class="fa-solid fa-sliders"></i><span>Settings & customization</span></button>';
    sidebar.appendChild(footer);
    footer.querySelectorAll("[data-va-nav]").forEach(button=>button.addEventListener("click",()=>{
      if(typeof window.navigateTo==="function")window.navigateTo(button.dataset.vaNav);
      view.classList.remove("va-chat-sidebar-open");
    }));
    document.addEventListener("click",event=>{
      if(!event.target.closest("#chatSidebar")&&!event.target.closest("#vaSidebarToggle"))view.classList.remove("va-chat-sidebar-open");
    });
  }
  if(document.readyState==="loading")document.addEventListener("DOMContentLoaded",installVectorWorkspace,{once:true});else installVectorWorkspace();
  const original=window.buildChatWorkspace;
  if(typeof original==="function")window.buildChatWorkspace=function(){original();installVectorWorkspace();};
})();
