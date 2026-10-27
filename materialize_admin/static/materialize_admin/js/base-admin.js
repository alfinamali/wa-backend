function getCookie(name) {
    let cookieValue = null;
    if (document.cookie && document.cookie !== '') {
        const cookies = document.cookie.split(';');
        for (let i = 0; i < cookies.length; i++) {
        const cookie = cookies[i].trim();
        // Does this cookie string begin with the name we want?
        if (cookie.substring(0, name.length + 1) === (name + '=')) {
            cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
            break;
        }
        }
    }
    return cookieValue;
}

const BASE_URL = window.location.origin;

function formatRupiah(angka) {
    return new Intl.NumberFormat('id-ID', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(angka);
}

var notyf = new Notyf({
    duration: 2000,
    position: {
        x: 'right',
        y: 'top',
    },
    dismissible: true,
    types: [
        {
            type: 'info',
            background: config.colors.info,
            className: 'notyf__info',
            icon: {
                className: 'icon-base ri ri-information-fill icon-md text-white',
                tagName: 'i'
            }
        },
        {
            type: 'warning',
            background: config.colors.warning,
            className: 'notyf__warning',
            icon: {
                className: 'icon-base ri ri-alert-fill icon-md text-white',
                tagName: 'i'
            }
        },
        {
            type: 'success',
            background: config.colors.success,
            className: 'notyf__success',
            icon: {
                className: 'icon-base ri ri-checkbox-circle-fill icon-md text-white',
                tagName: 'i'
            }
        },
        {
            type: 'error',
            background: config.colors.danger,
            className: 'notyf__error',
            icon: {
                className: 'icon-base ri ri-close-circle-fill icon-md text-white',
                tagName: 'i'
            }
        }
    ]
});

function updateParentBadges() {
    $("#layout-menu .menu-item").each(function () {
        const $menuItem = $(this);
        const $submenu = $menuItem.children(".menu-sub");

        if (!$submenu.length) return;

        let total = 0;

        $submenu.find(".menu-badge").each(function () {
        const val = parseInt($(this).text(), 10);
            if (!isNaN(val)) {
                total += val;
            }
        });

        const $parentLink = $menuItem.children("a.menu-link.menu-toggle");

        // hapus badge lama
        $parentLink.find(".menu-badge").remove();

        if (total > 0) {
        const badge = $('<span>', {
            class: "menu-badge badge badge-center text-bg-danger rounded-pill ms-auto",
            text: total,
        });

        $parentLink.append(badge);
        }
    });
}

function initializeBadgeMenu() {
    $.get(URL_BADGE_MENU, function (data) {
        if (!Array.isArray(data)) return;

        // RESET: hapus semua badge lama dulu sebelum diproses ulang
        $("#layout-menu .menu-badge").remove();

        data.forEach(function (item) {
            const safeHref = CSS.escape(item.href);
            const $link = $(`#layout-menu a[href='${safeHref}']`);
            if ($link.length) {
                if (item.badge !== "0") {
                    const badge = $('<span>', {
                        class: "menu-badge badge badge-center text-bg-danger rounded-pill ms-auto",
                        text: item.badge
                    });
                    $link.append(badge);
                }
            }
        });
        updateParentBadges();
    })
    .fail(function (xhr, status, error) {
        console.error("Gagal memuat badge menu:", error);
    });
}


function loadNotificationCount() {
    $.get(URL_NOTIFICATION, { action: "count" }, function (res) {
        if (res.count > 0) {
        $('.dropdown-notifications > a.dropdown-toggle').append(`
            <span class="position-absolute top-0 start-50
                translate-middle-y badge badge-dot bg-danger mt-2 border"></span>`);
        $('.dropdown-notifications .notification-count').text(`${res.count} New`);
        } else {
        // Hapus badge jika tidak ada notifikasi
        $('.dropdown-notifications > a.dropdown-toggle .badge-dot').remove();
        $('.dropdown-notifications .notification-count').text('-');
        }
    }).fail(function() {
        console.error("Gagal memuat jumlah notifikasi");
    });
}

function loadNotifications() {
    $(".dropdown-notifications-list ul").html(`
        <div class="text-center my-3">
        <div class="spinner-grow text-primary" role="status">
            <span class="visually-hidden">Loading...</span>
        </div>
        </div>
    `);
    $.get(URL_NOTIFICATION, { action: "list" }, function (res) {
        let html = ``;

        if (res.notifications.length === 0) {
        html = `<li class="dropdown-item text-muted text-center">
                    Tidak ada notifikasi
                </li>`;
        } else {
        res.notifications.forEach(function (n) {
            html += `
            <li class="list-group-item list-group-item-action dropdown-notifications-item-clickable waves-effect" data-id="${n.id}" data-url="${n.deep_link}">
                <div class="d-flex">
                <div class="flex-shrink-0 me-3">
                    <div class="avatar">
                    <span class="avatar-initial rounded-circle bg-label-primary">
                        <i class="icon-base ri ri-notification-2-line"></i>
                    </span>
                    </div>
                </div>
                <div class="flex-grow-1">
                    <h6 class="small mb-1">${n.title}</h6>
                    <small class="mb-1 d-block text-body">${n.message}</small>
                    <small class="text-body-secondary">${n.time_ago || n.created_at}</small>
                </div>
                <div class="flex-shrink-0 dropdown-notifications-actions">
                    <a href="javascript:void(0)" class="dropdown-notifications-read">
                    ${n.is_read ? `` : `<span class="badge badge-dot bg-primary"></span>`}
                    </a>
                </div>
                </div>
            </li>
            `;
        });
        }

        $(".dropdown-notifications-list ul").html(html);
    });
}

$(document).on("click", ".dropdown-notifications-item-clickable", function () {
    const notifId = $(this).data("id");
    const notifUrl = $(this).data("url");

    if (notifUrl) {
        $.get(URL_NOTIFICATION, { action: "mark_read", id: notifId }, function () {
        window.location.href = notifUrl;
        });
        return;
    }

    $.get(URL_NOTIFICATION, { action: "mark_read", id: notifId }, function () {
        loadNotificationCount();
        loadNotifications();
    });
});

$("#mark-all-as-read").on("click", function (e) {
    e.preventDefault();

    $.get(URL_NOTIFICATION, { action: "mark_all_read" }, function () {
        loadNotificationCount();
        loadNotifications();
    });
    });

function initializeAppNotif() {
    // URL kosong = fitur badge/notifikasi tidak dipakai (lihat admin/base.html)
    if (typeof URL_BADGE_MENU !== "undefined" && URL_BADGE_MENU) initializeBadgeMenu();
    if (typeof URL_NOTIFICATION !== "undefined" && URL_NOTIFICATION) {
        loadNotificationCount();
        loadNotifications();
    }
}

$(function () {
    initializeAppNotif();
});
