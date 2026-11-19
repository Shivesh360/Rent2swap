/**
 * Rent It Bro: An AI-Powered Rental, Selling & Exchange Marketplace
 * Phase 2 Client-Side JavaScript
 */

document.addEventListener("DOMContentLoaded", function () {
  // 1. Password Confirmation Check on Register Form
  const registerForm = document.getElementById("registerForm");
  const passwordInput = document.getElementById("password");
  const confirmPasswordInput = document.getElementById("confirm_password");
  const passwordMismatchNotice = document.getElementById("passwordMismatchNotice");

  if (confirmPasswordInput && passwordInput) {
    function checkPasswordsMatch() {
      if (confirmPasswordInput.value.length > 0) {
        if (passwordInput.value !== confirmPasswordInput.value) {
          confirmPasswordInput.classList.add("is-invalid");
          confirmPasswordInput.classList.remove("is-valid");
          if (passwordMismatchNotice) passwordMismatchNotice.classList.remove("d-none");
        } else {
          confirmPasswordInput.classList.remove("is-invalid");
          confirmPasswordInput.classList.add("is-valid");
          if (passwordMismatchNotice) passwordMismatchNotice.classList.add("d-none");
        }
      } else {
        confirmPasswordInput.classList.remove("is-invalid", "is-valid");
        if (passwordMismatchNotice) passwordMismatchNotice.classList.add("d-none");
      }
    }

    confirmPasswordInput.addEventListener("input", checkPasswordsMatch);
    passwordInput.addEventListener("input", checkPasswordsMatch);
  }

  // 2. Client-side Form Trim & CSRF token injection on submit
  const csrfMeta = document.querySelector("meta[name='csrf-token']");
  const globalCsrfToken = csrfMeta ? csrfMeta.getAttribute("content") : "";
  const forms = document.querySelectorAll("form");
  forms.forEach(function (form) {
    if (globalCsrfToken && (form.getAttribute("method") || "").toUpperCase() === "POST") {
      if (!form.querySelector("input[name='csrf_token']")) {
        const csrfInput = document.createElement("input");
        csrfInput.type = "hidden";
        csrfInput.name = "csrf_token";
        csrfInput.value = globalCsrfToken;
        form.prepend(csrfInput);
      }
    }
    form.addEventListener("submit", function () {
      if (globalCsrfToken && (form.getAttribute("method") || "").toUpperCase() === "POST") {
        let csrfInput = form.querySelector("input[name='csrf_token']");
        if (!csrfInput) {
          csrfInput = document.createElement("input");
          csrfInput.type = "hidden";
          csrfInput.name = "csrf_token";
          csrfInput.value = globalCsrfToken;
          form.prepend(csrfInput);
        }
      }
      const inputs = form.querySelectorAll("input[type='text'], input[type='email']");
      inputs.forEach(function (input) {
        input.value = input.value.trim();
      });
    });
  });

  // 3. Auto-dismiss flash messages after 6 seconds
  const alerts = document.querySelectorAll(".alert-dismissible");
  alerts.forEach(function (alert) {
    setTimeout(function () {
      if (typeof bootstrap !== 'undefined' && bootstrap.Alert) {
        const bsAlert = bootstrap.Alert.getOrCreateInstance(alert);
        if (bsAlert) {
          bsAlert.close();
        }
      }
    }, 6000);
  });

  // 4. Multi-Image Upload handled by multi-image-uploader.js (accumulative selection, remove buttons)

  // 5. Category Chips Quick Filter on index page
  const categoryChips = document.querySelectorAll(".category-chip");
  categoryChips.forEach(chip => {
    chip.addEventListener("click", function () {
      categoryChips.forEach(c => c.classList.remove("active"));
      this.classList.add("active");
    });
  });

  // 6. Theme Switching Engine (Dark / Light Mode)
  const themeToggleBtn = document.getElementById("theme-toggle-btn");
  const themeSunIcon = themeToggleBtn ? themeToggleBtn.querySelector(".theme-icon-sun") : null;
  const themeMoonIcon = themeToggleBtn ? themeToggleBtn.querySelector(".theme-icon-moon") : null;

  function applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    try {
      localStorage.setItem("r2s-theme", theme);
    } catch (e) {}

    if (themeToggleBtn) {
      if (theme === "dark") {
        themeToggleBtn.setAttribute("aria-label", "Switch to Light Mode");
        themeToggleBtn.setAttribute("title", "Switch to Light Mode");
        if (themeSunIcon) themeSunIcon.classList.remove("d-none");
        if (themeMoonIcon) themeMoonIcon.classList.add("d-none");
      } else {
        themeToggleBtn.setAttribute("aria-label", "Switch to Dark Mode");
        themeToggleBtn.setAttribute("title", "Switch to Dark Mode");
        if (themeSunIcon) themeSunIcon.classList.add("d-none");
        if (themeMoonIcon) themeMoonIcon.classList.remove("d-none");
      }
    }
  }

  // Initialize theme based on current attribute or system
  const currentTheme = document.documentElement.getAttribute("data-theme") || "dark";
  applyTheme(currentTheme);

  if (themeToggleBtn) {
    themeToggleBtn.addEventListener("click", function () {
      const activeTheme = document.documentElement.getAttribute("data-theme") || "dark";
      const nextTheme = activeTheme === "dark" ? "light" : "dark";
      applyTheme(nextTheme);
    });

    themeToggleBtn.addEventListener("keydown", function (e) {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        themeToggleBtn.click();
      }
    });
  }

  if (window.matchMedia) {
    window.matchMedia("(prefers-color-scheme: light)").addEventListener("change", function (e) {
      if (!localStorage.getItem("r2s-theme")) {
        applyTheme(e.matches ? "light" : "dark");
      }
    });
  }

  // 7. Compact Profile Dropdown & Logout Interaction Handling
  const userMenuBtn = document.getElementById("user-menu-btn");
  const profileDropdown = document.getElementById("user-profile-dropdown");
  const navbarCollapseEl = document.getElementById("navbarContent");

  function closeProfileDropdown(restoreFocus) {
    if (!userMenuBtn || typeof bootstrap === "undefined" || !bootstrap.Dropdown) return;
    const bsDropdown = bootstrap.Dropdown.getInstance(userMenuBtn);
    if (bsDropdown) {
      bsDropdown.hide();
      if (restoreFocus) userMenuBtn.focus();
    }
  }

  if (userMenuBtn && profileDropdown) {
    // Ensure Escape key closes profile dropdown cleanly
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" || e.key === "Esc") {
        closeProfileDropdown(true);
      }
    });

    // Close mobile navbar drawer if user opens the top-right profile menu
    userMenuBtn.addEventListener("click", function () {
      if (navbarCollapseEl && navbarCollapseEl.classList.contains("show") && typeof bootstrap !== "undefined" && bootstrap.Collapse) {
        const bsCollapse = bootstrap.Collapse.getInstance(navbarCollapseEl);
        if (bsCollapse) bsCollapse.hide();
      }
    });

    // Close dropdown cleanly after selecting any option
    const dropdownItems = profileDropdown.querySelectorAll(".dropdown-item");
    dropdownItems.forEach(function (item) {
      item.addEventListener("click", function () {
        closeProfileDropdown(false);
      });
    });
  }

  // Clear temporary user-specific cached state when logging out
  ["logout-form", "mobile-logout-form"].forEach(function (formId) {
    const logoutForm = document.getElementById(formId);
    if (logoutForm) {
      logoutForm.addEventListener("submit", function () {
        try {
          sessionStorage.clear();
        } catch (e) {}
      });
    }
  });
});

(function(){
  var NS='http://www.w3.org/2000/svg',B=420;
  var mouse=null,curPass=null,curText=null,hoverBtn=null,active=null,rafId=0,api=null;
  function E(tag,attrs,parent){var n=document.createElementNS(NS,tag);for(var k in attrs)n.setAttribute(k,attrs[k]);if(parent)parent.appendChild(n);return n;}

  /* ---------- input detection (page ke kisi bhi login form ke saath chalta hai) ---------- */
  function sel(){var r=document.getElementById('r2cScene');return r?r.getAttribute('data-password'):null;}
  function isPass(el){
    var s=sel();if(s){try{if(el.matches(s))return true;}catch(e){}}
    return el.type==='password'||/pass/i.test([el.name,el.id,el.placeholder,el.getAttribute('autocomplete')].join(' '));
  }
  document.addEventListener('focusin',function(e){
    var el=e.target;if(!el||(el.tagName!=='INPUT'&&el.tagName!=='TEXTAREA'))return;
    if(/^(checkbox|radio|button|submit)$/.test(el.type))return;
    if(isPass(el)){curPass=el;curText=null;}else{curText=el;curPass=null;}
  });
  document.addEventListener('focusout',function(){
    setTimeout(function(){
      var a=document.activeElement;
      if(curPass&&!(curPass===a||(curPass.parentNode&&curPass.parentNode.contains(a))))curPass=null;
      if(curText&&curText!==a)curText=null;
    },0);
  });
  document.addEventListener('pointermove',function(e){
    mouse={x:e.clientX,y:e.clientY};
    var t=e.target;hoverBtn=null;
    if(t&&t.closest){
      var b=t.closest('button,input[type="submit"],[role="button"]');
      if(b){var tx=(b.textContent||b.value||'');
        if((b.type==='submit'||/log\s?in|sign\s?in|login|submit/i.test(tx))&&!/google|facebook|github|apple/i.test(tx))hoverBtn=b;}
    }
  });
  document.documentElement.addEventListener('mouseleave',function(){mouse=null;hoverBtn=null;});

  /* ---------- characters ---------- */
  var CFG=[
    {k:'purple',cx:201.5,top:120,w:139,eyeY:36,dx:22,r:8,pr:4,mY:58,mx:0,sc:1,spd:.18,delay:.00,ph:0,fk:1},
    {k:'orange',cx:155,top:308,w:230,eyeY:46,dx:38,r:0,pr:5.6,mY:68,mx:0,sc:0,spd:.20,delay:.15,ph:1.3,fk:1},
    {k:'black',cx:274,top:209,w:86,eyeY:40,dx:17,r:10,pr:5,mY:68,mx:0,sc:1,spd:.24,delay:.06,ph:2.1,fk:.7},
    {k:'yellow',cx:343.5,top:264,w:93,eyeY:34,dx:19,r:8,pr:4,mY:62,mx:0,sc:1,spd:.28,delay:.22,ph:3.4,fk:.65}
  ];
  var MOUTH={
    purple:{idle:'M-6 0Q0 4 6 0',happy:'M-9 -1Q0 10 9 -1',sad:'M-7 4Q0 -3 7 4'},
    orange:{idle:'M-14 0Q0 11 14 0',happy:'M-20 -3Q0 20 20 -3',sad:'M-16 7Q0 -6 16 7'},
    black:{idle:'M-8 0Q0 7 8 0',happy:'M-10 -1Q0 12 10 -1',sad:'M-8 4Q0 -3 8 4'},
    yellow:{idle:'M-8 0Q0 5 8 0',happy:'M-10 -1Q0 11 10 -1',sad:'M-8 4Q0 -3 8 4'}
  };
  var ARC_HAPPY='M-8 3Q0 -7 8 3',ARC_SHUT='M-8 -1Q0 6 8 -1';

  function mkEye(parent,c,x){
    var g=E('g',{transform:'translate('+x+' '+c.eyeY+')'},parent),eb=E('g',{},g);
    if(c.sc)E('circle',{r:c.r,'class':'r2c-sclera'},eb);
    var pup=E('circle',{r:c.pr,'class':'r2c-pup'},eb);
    var arc=E('path',{d:ARC_SHUT,fill:'none','class':'r2c-arc'+(c.k==='black'?' r2c-m-l':''),'stroke-width':3,'stroke-linecap':'round',opacity:0},g);
    return {eb:eb,pup:pup,arc:arc};
  }
  function purplePath(c,b){
    var L=c.cx-c.w/2,R=c.cx+c.w/2,T=c.top,tilt=b*.18,cy=B-(B-T)*.55;
    return 'M'+L+' '+B+'L'+R+' '+B+'Q'+(R+b*.05)+' '+cy+' '+(R+b)+' '+(T+tilt)+'L'+(L+b)+' '+(T-tilt)+'Q'+(L+b*.05)+' '+cy+' '+L+' '+B+'Z';
  }

  function start(root){
    root.innerHTML='';
    var svg=E('svg',{viewBox:'0 0 440 430','class':'r2c-svg','aria-hidden':'true'});root.appendChild(svg);
    if(navigator.hardwareConcurrency&&navigator.hardwareConcurrency<=2&&root.className.indexOf('r2c-lite')<0)root.className+=' r2c-lite';
    var defs=E('defs',{},svg);
    ['P','K','Y','O'].forEach(function(n){
      var g=E('linearGradient',{id:'r2g'+n,x1:0,y1:0,x2:.35,y2:1},defs);
      E('stop',{offset:0,'class':'r2c-s-'+n+'1'},g);E('stop',{offset:1,'class':'r2c-s-'+n+'2'},g);
    });
    var sh=E('linearGradient',{id:'r2gSheen',x1:0,y1:0,x2:.7,y2:.9},defs);
    E('stop',{offset:0,'stop-color':'#fff','stop-opacity':.38},sh);E('stop',{offset:.55,'stop-color':'#fff','stop-opacity':0},sh);
    E('ellipse',{cx:215,cy:425,rx:205,ry:8,'class':'r2c-shadow'},svg);
    var reduce=window.matchMedia&&window.matchMedia('(prefers-reduced-motion:reduce)').matches;
    var cs=[];
    CFG.forEach(function(c,i){
      var o={c:c,i:i,lx:0,ly:0,dy:reduce?0:-560,vy:0,sq:0,landed:!!reduce,rot0:(i%2?1:-1)*(14+Math.random()*18),
        blinkAt:2200+Math.random()*2500,blinkStart:-1,winkAt:6000+Math.random()*3000,winkStart:-1,wink:0,mm:''};
      o.g=E('g',{},svg);
      var host=o.g;
      if(c.k==='purple'){o.body=E('path',{'class':'r2c-body r2c-glow r2c-purple',d:purplePath(c,0)},o.g);o.sheen=E('path',{'class':'r2c-sheen',d:purplePath(c,0)},o.g);}
      else{
        o.sk=E('g',{},o.g);host=o.sk;
        var shp={orange:['path',{d:'M40 420A115 112 0 0 1 270 420Z'}],black:['rect',{x:231,y:209,width:86,height:211,rx:5}],yellow:['path',{d:'M297 420V310.5A46.5 46.5 0 0 1 390 310.5V420Z'}]}[c.k];
        var a1={'class':'r2c-body r2c-glow r2c-'+c.k},a2={'class':'r2c-sheen'};
        for(var q in shp[1]){a1[q]=shp[1][q];a2[q]=shp[1][q];}
        E(shp[0],a1,host);E(shp[0],a2,host);
      }
      o.face=E('g',{},host);
      o.eyes=[mkEye(o.face,c,-c.dx),mkEye(o.face,c,c.dx)];
      o.mouth=E('path',{'class':'r2c-m'+(c.k==='black'?' r2c-m-l':''),fill:'none','stroke-width':3.2,'stroke-linecap':'round','stroke-linejoin':'round'},o.face);
      cs.push(o);
    });

    var t0=performance.now(),last=t0,mood='idle',moodUntil=0,moodStart=0,aw=0,clg=0,hpg=0,cu=0,sd=0;

    function tick(now){
      if(!document.body.contains(root)){active=null;api=null;return;}
      var dtf=Math.min((now-last)/16.667,3);last=now;
      var t=(now-t0)/1000,r=svg.getBoundingClientRect(),s=(r.width/440)||1;
      if(moodUntil&&now>moodUntil){mood='idle';moodUntil=0;}
      var passOn=!!curPass&&document.body.contains(curPass),passVis=passOn&&curPass.type!=='password';
      var away=passOn&&mood==='idle';
      var tp=mouse,curious=false;
      if(curText&&document.body.contains(curText)){
        var b=curText.getBoundingClientRect();
        tp={x:b.left+Math.min(10+(curText.value||'').length*8.5,b.width-26),y:b.top+b.height/2};
      }else if(hoverBtn&&document.body.contains(hoverBtn)&&!passOn){
        var bb=hoverBtn.getBoundingClientRect();tp={x:bb.left+bb.width/2,y:bb.top+bb.height/2};curious=true;
      }
      aw+=((away?1:0)-aw)*.15;
      clg+=((passVis&&mood==='idle'?1:0)-clg)*.25;
      hpg+=((mood==='happy'?1:0)-hpg)*.3;
      cu+=((curious?1:0)-cu)*.25;
      sd+=((mood==='sad'?1:0)-sd)*.2;
      var hs=(now-moodStart)/1000,
          shake=mood==='sad'?Math.sin(hs*40)*Math.max(0,1-hs/.6)*7:0;

      for(var i=0;i<cs.length;i++){
        var o=cs[i],c=o.c;
        /* entrance: upar se girna + bounce */
        if(!o.landed&&(now-t0)>c.delay*1000){
          o.vy+=2.9*dtf;o.dy+=o.vy*dtf;
          if(o.dy>=0){o.dy=0;o.sq=Math.min(.2,Math.abs(o.vy)*.012);o.vy=-o.vy*.36;if(Math.abs(o.vy)<2.6){o.vy=0;o.landed=true;}}
        }
        o.sq*=Math.pow(.82,dtf);
        var rot=o.landed?0:o.rot0*Math.max(0,Math.min(1,-o.dy/560));

        /* kahan dekhna hai */
        var fx=r.left+c.cx*s,fy=r.top+(c.top+c.eyeY)*s,tl=0,tv=0;
        if(mood==='sad'){tl=0;tv=.9;}
        else if(away){tl=-1;tv=-.15;}
        else if(tp){
          var dx=tp.x-fx,dy=tp.y-fy,d=Math.sqrt(dx*dx+dy*dy)||1,k=Math.min(d/(r.width*.55),1);tl=dx/d*k;tv=dy/d*k;
        }else{tl=Math.sin(t*1+c.ph)*.3;}
        o.lx+=(tl-o.lx)*c.spd;o.ly+=(tv-o.ly)*c.spd;

        /* body: saans, hop, jhukna */
        var hop=0;
        if(mood==='happy'){hop=-Math.abs(Math.sin(hs*13+i*.6))*18*Math.max(0,1-hs/2.4);}
        var sy=1+(reduce?0:Math.sin(t*3+c.ph)*.013)-o.sq-sd*.05,sx=1+o.sq*.6;
        o.g.setAttribute('transform','translate('+shake+' '+(o.dy+hop)+') rotate('+rot+' '+c.cx+' '+B+') translate('+c.cx+' '+B+') scale('+sx+' '+sy+') translate('+(-c.cx)+' '+(-B)+')');

        var fxo=(o.lx*12-aw*6)*c.fk,fyo=o.ly*7*c.fk;
        if(c.k==='purple'){
          var bb2=o.lx*30+(reduce?0:Math.sin(t*1.3)*3)-aw*18-sd*8;
          var pd=purplePath(c,bb2);o.body.setAttribute('d',pd);o.sheen.setAttribute('d',pd);
          var deg=Math.atan2(bb2*.18*2,c.w)*57.3;
          o.face.setAttribute('transform','translate('+(c.cx+bb2)+' '+c.top+') rotate('+deg+') translate('+fxo+' '+fyo+')');
        }else{
          var sk=o.lx*(c.k==='black'?3.5:3)-aw*2-sd*1;
          o.sk.setAttribute('transform','translate('+c.cx+' '+B+') skewX('+(-sk)+') translate('+(-c.cx)+' '+(-B)+')');
          o.face.setAttribute('transform','translate('+c.cx+' '+c.top+') translate('+fxo+' '+fyo+')');
        }

        /* aankhein: pupil, blink, band/khushi */
        var pm=c.sc?(c.r-c.pr-1):3;
        if(o.blinkStart<0&&now>t0+o.blinkAt)o.blinkStart=now;
        var bs=1;
        if(o.blinkStart>=0){var p=(now-o.blinkStart)/130;if(p>=1){o.blinkStart=-1;o.blinkAt=(now-t0)+2200+Math.random()*3500;}else bs=1-Math.sin(p*Math.PI)*.92;}
        if(c.k==='orange'){
          if(o.winkStart<0&&now>t0+o.winkAt)o.winkStart=now;
          o.wink=0;
          if(o.winkStart>=0){var pw=(now-o.winkStart)/340;if(pw>=1){o.winkStart=-1;o.winkAt=(now-t0)+6000+Math.random()*4000;}else o.wink=Math.sin(pw*Math.PI);}
        }
        for(var e=0;e<2;e++){
          var ey=o.eyes[e],ca=Math.max(clg,hpg,(c.k==='orange'&&e===0)?o.wink:0);
          ey.pup.setAttribute('transform','translate('+(o.lx*pm).toFixed(2)+' '+(o.ly*pm).toFixed(2)+')');
          ey.pup.setAttribute('r',c.pr*(1+.25*cu));
          ey.eb.setAttribute('transform','scale(1 '+bs+')');
          ey.eb.setAttribute('opacity',1-ca);
          ey.arc.setAttribute('opacity',ca);
          ey.arc.setAttribute('d',hpg>.5?ARC_HAPPY:ARC_SHUT);
        }
        /* muh */
        var mk=mood==='happy'?'happy':(mood==='sad'?'sad':'idle');
        if(o.mm!==mk){o.mm=mk;o.mouth.setAttribute('d',MOUTH[c.k][mk]);}
        o.mouth.setAttribute('transform','translate('+(c.mx+o.lx*3)+' '+c.mY+')');
      }
      rafId=requestAnimationFrame(tick);
    }
    api={
      happy:function(ms){mood='happy';moodStart=performance.now();moodUntil=moodStart+(ms||2600);},
      sad:function(ms){mood='sad';moodStart=performance.now();moodUntil=moodStart+(ms||1800);},
      reset:function(){mood='idle';moodUntil=0;}
    };
    active=root;rafId=requestAnimationFrame(tick);
  }
  function check(){
    var el=document.getElementById('r2cScene');
    if(el&&el!==active){cancelAnimationFrame(rafId);start(el);}
    else if(!el&&active){cancelAnimationFrame(rafId);active=null;api=null;}
  }
  var pub={
    happy:function(ms){if(api)api.happy(ms);},sad:function(ms){if(api)api.sad(ms);},
    reset:function(){if(api)api.reset();},wave:function(){}
  };
  window.Creatures=pub;window.PandaMascot=pub; /* purane hooks bhi chalte rahenge */

  /* ---------- site ke dark/light toggle ke saath sync ---------- */
  function sync(){
    var root=document.getElementById('r2s');if(!root)return;
    var h=document.documentElement,b=document.body;
    var dark=h.classList.contains('dark')||b.classList.contains('dark')||h.getAttribute('data-theme')==='dark'||b.getAttribute('data-theme')==='dark';
    if(root.classList.contains('is-dark')!==dark)root.classList[dark?'add':'remove']('is-dark');
  }
  new MutationObserver(function(){check();sync();}).observe(document.body,{childList:true,subtree:true});
  var mo2=new MutationObserver(sync);
  mo2.observe(document.documentElement,{attributes:true,attributeFilter:['class','data-theme']});
  mo2.observe(document.body,{attributes:true,attributeFilter:['class','data-theme']});
  check();sync();
})();

/* Listing Card Multi-Image Gallery Controls (.r2l-card-media) */
(function () {
  function cycleCardMedia(btn, step) {
    var media = btn.closest(".r2l-card-media");
    if (!media) return;
    var raw = media.getAttribute("data-gallery");
    if (!raw) return;

    var gallery = null;
    try {
      gallery = JSON.parse(raw);
    } catch (err) {
      return;
    }
    if (!Array.isArray(gallery) || gallery.length <= 1) return;

    var curr = parseInt(media.getAttribute("data-idx") || "0", 10);
    if (isNaN(curr)) curr = 0;
    var next = (curr + step + gallery.length) % gallery.length;
    media.setAttribute("data-idx", String(next));

    var img = media.querySelector(".r2l-card-img");
    if (img && gallery[next]) {
      img.style.opacity = "0.78";
      img.src = gallery[next];
      img.onload = function () {
        img.style.opacity = "1";
      };
      setTimeout(function () {
        img.style.opacity = "1";
      }, 180);
    }

    var currNum = media.querySelector(".r2l-photo-curr");
    if (currNum) currNum.textContent = String(next + 1);

    var dots = media.querySelectorAll(".r2l-gallery-dot");
    if (dots && dots.length) {
      var activeDotIdx = Math.min(next, dots.length - 1);
      dots.forEach(function (dot, i) {
        dot.classList.toggle("is-active", i === activeDotIdx);
      });
    }
  }

  document.addEventListener("click", function (e) {
    var prevBtn = e.target.closest && e.target.closest(".r2l-gallery-prev");
    var nextBtn = e.target.closest && e.target.closest(".r2l-gallery-next");
    var btn = prevBtn || nextBtn;
    if (!btn) return;
    e.preventDefault();
    e.stopPropagation();
    cycleCardMedia(btn, nextBtn ? 1 : -1);
  });

  document.addEventListener("keydown", function (e) {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    var active = document.activeElement;
    if (!active || !active.classList || !active.classList.contains("r2l-gallery-btn")) return;
    e.preventDefault();
    cycleCardMedia(active, e.key === "ArrowRight" ? 1 : -1);
  });
})();
