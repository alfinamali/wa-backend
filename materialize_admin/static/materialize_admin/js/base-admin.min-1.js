function getCookie(i){let t=null;if(document.cookie&&""!==document.cookie){let a=document.cookie.split(";");for(let n=0;n<a.length;n++){let o=a[n].trim();if(o.substring(0,i.length+1)===i+"="){t=decodeURIComponent(o.substring(i.length+1));break}}}return t}const BASE_URL=window.location.origin;function formatRupiah(i){return new Intl.NumberFormat("id-ID",{minimumFractionDigits:2,maximumFractionDigits:2}).format(i)}var notyf=new Notyf({duration:2e3,position:{x:"right",y:"top"},dismissible:!0,types:[{type:"info",background:config.colors.info,className:"notyf__info",icon:{className:"icon-base ri ri-information-fill icon-md text-white",tagName:"i"}},{type:"warning",background:config.colors.warning,className:"notyf__warning",icon:{className:"icon-base ri ri-alert-fill icon-md text-white",tagName:"i"}},{type:"success",background:config.colors.success,className:"notyf__success",icon:{className:"icon-base ri ri-checkbox-circle-fill icon-md text-white",tagName:"i"}},{type:"error",background:config.colors.danger,className:"notyf__error",icon:{className:"icon-base ri ri-close-circle-fill icon-md text-white",tagName:"i"}}]});function updateParentBadges(){$("#layout-menu .menu-item").each(function(){let i=$(this),t=i.children(".menu-sub");if(!t.length)return;let a=0;t.find(".menu-badge").each(function(){let i=parseInt($(this).text(),10);isNaN(i)||(a+=i)});let n=i.children("a.menu-link.menu-toggle");if(n.find(".menu-badge").remove(),a>0){let o=$("<span>",{class:"menu-badge badge badge-center text-bg-danger rounded-pill ms-auto",text:a});n.append(o)}})}function initializeBadgeMenu(){$.get(URL_BADGE_MENU,function(i){Array.isArray(i)&&(i.forEach(function(i){let t=CSS.escape(i.href),a=$(`#layout-menu a[href='${t}']`);if(a.length&&(a.find(".menu-badge").remove(),"0"!==i.badge)){let n=$("<span>",{class:"menu-badge badge badge-center text-bg-danger rounded-pill ms-auto",text:i.badge});a.append(n)}}),updateParentBadges())}).fail(function(i,t,a){console.error("Gagal memuat badge menu:",a)})}function loadNotificationCount(){$.get(URL_NOTIFICATION,{action:"count"},function(i){i.count>0?($(".dropdown-notifications > a.dropdown-toggle").append(`
            <span class="position-absolute top-0 start-50
                translate-middle-y badge badge-dot bg-danger mt-2 border"></span>`),$(".dropdown-notifications .notification-count").text(`${i.count} New`)):($(".dropdown-notifications > a.dropdown-toggle .badge-dot").remove(),$(".dropdown-notifications .notification-count").text("-"))}).fail(function(){console.error("Gagal memuat jumlah notifikasi")})}function loadNotifications(){$(".dropdown-notifications-list ul").html(`
        <div class="text-center my-3">
        <div class="spinner-grow text-primary" role="status">
            <span class="visually-hidden">Loading...</span>
        </div>
        </div>
    `),$.get(URL_NOTIFICATION,{action:"list"},function(i){let t="";0===i.notifications.length?t=`<li class="dropdown-item text-muted text-center">
                    Tidak ada notifikasi
                </li>`:i.notifications.forEach(function(i){t+=`
            <li class="list-group-item list-group-item-action dropdown-notifications-item-clickable waves-effect" data-id="${i.id}" data-url="${i.deep_link}">
                <div class="d-flex">
                <div class="flex-shrink-0 me-3">
                    <div class="avatar">
                    <span class="avatar-initial rounded-circle bg-label-primary">
                        <i class="icon-base ri ri-notification-2-line"></i>
                    </span>
                    </div>
                </div>
                <div class="flex-grow-1">
                    <h6 class="small mb-1">${i.title}</h6>
                    <small class="mb-1 d-block text-body">${i.message}</small>
                    <small class="text-body-secondary">${i.time_ago||i.created_at}</small>
                </div>
                <div class="flex-shrink-0 dropdown-notifications-actions">
                    <a href="javascript:void(0)" class="dropdown-notifications-read">
                    ${i.is_read?"":'<span class="badge badge-dot bg-primary"></span>'}
                    </a>
                </div>
                </div>
            </li>
            `}),$(".dropdown-notifications-list ul").html(t)})}function initializeAppNotif(){initializeBadgeMenu(),loadNotificationCount(),loadNotifications()}$(document).on("click",".dropdown-notifications-item-clickable",function(){let i=$(this).data("id"),t=$(this).data("url");if(t){$.get(URL_NOTIFICATION,{action:"mark_read",id:i},function(){window.location.href=t});return}$.get(URL_NOTIFICATION,{action:"mark_read",id:i},function(){loadNotificationCount(),loadNotifications()})}),$("#mark-all-as-read").on("click",function(i){i.preventDefault(),$.get(URL_NOTIFICATION,{action:"mark_all_read"},function(){loadNotificationCount(),loadNotifications()})}),$(function(){initializeAppNotif()});