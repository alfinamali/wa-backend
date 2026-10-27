from django import template
from django.utils.safestring import mark_safe

register = template.Library()


@register.simple_tag
def get_menus(user=None, path=None):
    menus = ""
    try:
        from django.conf import settings
        from django.utils.module_loading import import_string

        # mis. MATERIALIZE_ADMIN = {"menu": "config.admin_menu.get_menu_items"}
        get_menu_items = import_string(getattr(settings, "MATERIALIZE_ADMIN", {}).get("menu", "menus.get_menu_items"))
        menu_list = get_menu_items(user)
    except (ImportError, AttributeError):
        menu_list = [
            {"title": "Beranda", "url": "/", "icon": "ri ri-home-smile-line"},
        ]
    for item in menu_list:
        if item.get("header", None):
            menus += f"""
                <li class="menu-header fw-light mt-2">
                    <span class="menu-header-text">{item["title"]}</span>
                </li>
                """
        else:
            children = item.get("children", None)
            if children:

                def is_active_menu(child):
                    # Cek URL child
                    if child.get("url", "#") == path:
                        return True
                    # Cek jika punya children (level 3)
                    if "children" in child:
                        return any(
                            grandchild.get("url", "#") == path
                            for grandchild in child["children"]
                        )
                    return False

                is_active = any(is_active_menu(child) for child in children)

                menus += f"""
                    <li class="menu-item {"active open" if is_active else ""}">
                        <a href="javascript:void(0);" class="menu-link menu-toggle">
                            <i class="menu-icon tf-icons {item["icon"]}"></i>
                            <div>{item["title"]}</div>
                        </a>
                        <ul class="menu-sub">
                """

                for child in item["children"]:
                    # cek apakah URL child cocok
                    is_active_child = child.get("url", "#") == path

                    children_child = child.get("children", None)
                    if children_child:
                        # cek apakah salah satu URL dalam children_child cocok
                        is_active_grandchild = any(
                            c.get("url", "#") == path for c in children_child
                        )
                        is_active_combined = is_active_child or is_active_grandchild
                        icon = child.get("icon", "")
                        menus += f"""
                            <li class="menu-item {"active open" if is_active_combined else ""}">
                                <a href="javascript:void(0);" class="menu-link menu-toggle">
                                    {f'<i class="menu-icon tf-icons {icon}"></i>' if icon else ""}
                                    <div>{child["title"]}</div>
                                </a>
                                <ul class="menu-sub">
                        """

                        for child_child in children_child:
                            is_active_grandchild_item = (
                                child_child.get("url", "#") == path
                            )
                            icon = child_child.get("icon", "")
                            menus += f"""
                                <li class="menu-item {"active" if is_active_grandchild_item else ""}">
                                    <a href="{child_child.get("url", "#")}" class="menu-link">
                                        {f'<i class="menu-icon tf-icons {icon}"></i>' if icon else ""}
                                        <div>{child_child.get("title", "")}</div>
                                    </a>
                                </li>
                            """
                        menus += """
                                </ul>
                            </li>
                        """
                    else:
                        menus += f"""
                            <li class="menu-item {"active" if is_active_child else ""}">
                                <a href="{child.get("url", "#")}" class="menu-link">
                                    <div>{child.get("title", "")}</div>
                                </a>
                            </li>
                        """

                menus += """
                    </ul>
                </li>
                """
            else:
                is_active = item["url"] == path
                menus += f"""
                    <li class="menu-item {"active" if is_active else ""}">
                        <a href="{item["url"]}" class="menu-link">
                            <i class="menu-icon tf-icons {item["icon"]}"></i>
                            <div data-i18n="{item["title"]}">{item["title"]}</div>
                        </a>
                    </li>
                    """
    return mark_safe(menus)
