"""Linux evdev key codes and their names, as InputActions spells them (it matches keys by
scancode). Generated from linux/input-event-codes.h, keeping the names InputActions lists."""

NAMES = {
    1: "esc", 2: "1", 3: "2", 4: "3", 5: "4", 6: "5", 7: "6", 8: "7", 9: "8", 10: "9", 11: "0", 12: "minus", 13:
    "equal", 14: "backspace", 15: "tab", 16: "q", 17: "w", 18: "e", 19: "r", 20: "t", 21: "y", 22: "u", 23: "i",
    24: "o", 25: "p", 26: "leftbrace", 27: "rightbrace", 28: "enter", 29: "leftctrl", 30: "a", 31: "s", 32: "d",
    33: "f", 34: "g", 35: "h", 36: "j", 37: "k", 38: "l", 39: "semicolon", 40: "apostrophe", 41: "grave", 42:
    "leftshift", 43: "backslash", 44: "z", 45: "x", 46: "c", 47: "v", 48: "b", 49: "n", 50: "m", 51: "comma",
    52: "dot", 53: "slash", 54: "rightshift", 55: "kpasterisk", 56: "leftalt", 57: "space", 58: "capslock", 59:
    "f1", 60: "f2", 61: "f3", 62: "f4", 63: "f5", 64: "f6", 65: "f7", 66: "f8", 67: "f9", 68: "f10", 69:
    "numlock", 70: "scrolllock", 71: "kp7", 72: "kp8", 73: "kp9", 74: "kpminus", 75: "kp4", 76: "kp5", 77:
    "kp6", 78: "kpplus", 79: "kp1", 80: "kp2", 81: "kp3", 82: "kp0", 83: "kpdot", 85: "zenkakuhankaku", 86:
    "102nd", 87: "f11", 88: "f12", 89: "ro", 90: "katakana", 91: "hiragana", 92: "henkan", 93:
    "katakanahiragana", 94: "muhenkan", 95: "kpjpcomma", 96: "kpenter", 97: "rightctrl", 98: "kpslash", 99:
    "sysrq", 100: "rightalt", 101: "linefeed", 102: "home", 103: "up", 104: "pageup", 105: "left", 106: "right",
    107: "end", 108: "down", 109: "pagedown", 110: "insert", 111: "delete", 112: "macro", 113: "mute", 114:
    "volumedown", 115: "volumeup", 116: "power", 117: "kpequal", 118: "kpplusminus", 119: "pause", 120: "scale",
    121: "kpcomma", 122: "hangeul", 123: "hanja", 124: "yen", 125: "leftmeta", 126: "rightmeta", 127: "compose",
    128: "stop", 129: "again", 130: "props", 131: "undo", 132: "front", 133: "copy", 134: "open", 135: "paste",
    136: "find", 137: "cut", 138: "help", 139: "menu", 140: "calc", 141: "setup", 142: "sleep", 143: "wakeup",
    144: "file", 145: "sendfile", 146: "deletefile", 147: "xfer", 148: "prog1", 149: "prog2", 150: "www", 151:
    "msdos", 152: "coffee", 153: "rotate_display", 154: "cyclewindows", 155: "mail", 156: "bookmarks", 157:
    "computer", 158: "back", 159: "forward", 160: "closecd", 161: "ejectcd", 162: "ejectclosecd", 163:
    "nextsong", 164: "playpause", 165: "previoussong", 166: "stopcd", 167: "record", 168: "rewind", 169:
    "phone", 170: "iso", 171: "config", 172: "homepage", 173: "refresh", 174: "exit", 175: "move", 176: "edit",
    177: "scrollup", 178: "scrolldown", 179: "kpleftparen", 180: "kprightparen", 181: "new", 182: "redo", 183:
    "f13", 184: "f14", 185: "f15", 186: "f16", 187: "f17", 188: "f18", 189: "f19", 190: "f20", 191: "f21", 192:
    "f22", 193: "f23", 194: "f24", 200: "playcd", 201: "pausecd", 202: "prog3", 203: "prog4", 204:
    "all_applications", 205: "suspend", 206: "close", 207: "play", 208: "fastforward", 209: "bassboost", 210:
    "print", 211: "hp", 212: "camera", 213: "sound", 214: "question", 215: "email", 216: "chat", 217: "search",
    218: "connect", 219: "finance", 220: "sport", 221: "shop", 222: "alterase", 223: "cancel", 224:
    "brightnessdown", 225: "brightnessup", 226: "media", 227: "switchvideomode", 228: "kbdillumtoggle", 229:
    "kbdillumdown", 230: "kbdillumup", 231: "send", 232: "reply", 233: "forwardmail", 234: "save", 235:
    "documents", 236: "battery", 237: "bluetooth", 238: "wlan", 239: "uwb", 240: "unknown", 241: "video_next",
    242: "video_prev", 243: "brightness_cycle", 244: "brightness_auto", 245: "display_off", 246: "wwan", 247:
    "rfkill", 248: "micmute", 352: "ok", 353: "select", 354: "goto", 355: "clear", 356: "power2", 357: "option",
    358: "info", 359: "time", 360: "vendor", 361: "archive", 362: "program", 363: "channel", 364: "favorites",
    365: "epg", 366: "pvr", 367: "mhp", 368: "language", 369: "title", 370: "subtitle", 371: "angle", 372:
    "full_screen", 373: "mode", 374: "keyboard", 375: "aspect_ratio", 376: "pc", 377: "tv", 378: "tv2", 379:
    "vcr", 380: "vcr2", 381: "sat", 382: "sat2", 383: "cd", 384: "tape", 385: "radio", 386: "tuner", 387:
    "player", 388: "text", 389: "dvd", 390: "aux", 391: "mp3", 392: "audio", 393: "video", 394: "directory",
    395: "list", 396: "memo", 397: "calendar", 398: "red", 399: "green", 400: "yellow", 401: "blue", 402:
    "channelup", 403: "channeldown", 404: "first", 405: "last", 406: "ab", 407: "next", 408: "restart", 409:
    "slow", 410: "shuffle", 411: "break", 412: "previous", 413: "digits", 414: "teen", 415: "twen", 416:
    "videophone", 417: "games", 418: "zoomin", 419: "zoomout", 420: "zoomreset", 421: "wordprocessor", 422:
    "editor", 423: "spreadsheet", 424: "graphicseditor", 425: "presentation", 426: "database", 427: "news", 428:
    "voicemail", 429: "addressbook", 430: "messenger", 431: "displaytoggle", 432: "spellcheck", 433: "logoff",
    434: "dollar", 435: "euro", 436: "frameback", 437: "frameforward", 438: "context_menu", 439: "media_repeat",
    440: "10channelsup", 441: "10channelsdown", 442: "images", 444: "notification_center", 445: "pickup_phone",
    446: "hangup_phone", 448: "del_eol", 449: "del_eos", 450: "ins_line", 451: "del_line", 464: "fn", 465:
    "fn_esc", 466: "fn_f1", 467: "fn_f2", 468: "fn_f3", 469: "fn_f4", 470: "fn_f5", 471: "fn_f6", 472: "fn_f7",
    473: "fn_f8", 474: "fn_f9", 475: "fn_f10", 476: "fn_f11", 477: "fn_f12", 478: "fn_1", 479: "fn_2", 480:
    "fn_d", 481: "fn_e", 482: "fn_f", 483: "fn_s", 484: "fn_b", 485: "fn_right_shift", 497: "brl_dot1", 498:
    "brl_dot2", 499: "brl_dot3", 500: "brl_dot4", 501: "brl_dot5", 502: "brl_dot6", 503: "brl_dot7", 504:
    "brl_dot8", 505: "brl_dot9", 506: "brl_dot10", 512: "numeric_0", 513: "numeric_1", 514: "numeric_2", 515:
    "numeric_3", 516: "numeric_4", 517: "numeric_5", 518: "numeric_6", 519: "numeric_7", 520: "numeric_8", 521:
    "numeric_9", 522: "numeric_star", 523: "numeric_pound", 524: "numeric_a", 525: "numeric_b", 526:
    "numeric_c", 527: "numeric_d", 528: "camera_focus", 529: "wps_button", 530: "touchpad_toggle", 531:
    "touchpad_on", 532: "touchpad_off", 533: "camera_zoomin", 534: "camera_zoomout", 535: "camera_up", 536:
    "camera_down", 537: "camera_left", 538: "camera_right", 539: "attendant_on", 540: "attendant_off", 541:
    "attendant_toggle", 542: "lights_toggle", 560: "als_toggle", 561: "rotate_lock_toggle", 562:
    "refresh_rate_toggle", 576: "buttonconfig", 577: "taskmanager", 578: "journal", 579: "controlpanel", 580:
    "appselect", 581: "screensaver", 582: "voicecommand", 583: "assistant", 584: "kbd_layout_next", 585:
    "emoji_picker", 586: "dictate", 587: "camera_access_enable", 588: "camera_access_disable", 589:
    "camera_access_toggle", 592: "brightness_min", 593: "brightness_max", 608: "kbdinputassist_prev", 609:
    "kbdinputassist_next", 610: "kbdinputassist_prevgroup", 611: "kbdinputassist_nextgroup", 612:
    "kbdinputassist_accept", 613: "kbdinputassist_cancel", 614: "right_up", 615: "right_down", 616: "left_up",
    617: "left_down", 618: "root_menu", 619: "media_top_menu", 620: "numeric_11", 621: "numeric_12", 622:
    "audio_desc", 623: "3d_mode", 624: "next_favorite", 625: "stop_record", 626: "pause_record", 627: "vod",
    628: "unmute", 629: "fastreverse", 630: "slowreverse", 631: "data", 632: "onscreen_keyboard", 633:
    "privacy_screen_toggle", 634: "selective_screenshot", 635: "next_element", 636: "previous_element", 637:
    "autopilot_engage_toggle", 638: "mark_waypoint", 639: "sos", 640: "nav_chart", 641: "fishing_chart", 642:
    "single_range_radar", 643: "dual_range_radar", 644: "radar_overlay", 645: "traditional_sonar", 646:
    "clearvu_sonar", 647: "sidevu_sonar", 648: "nav_info", 649: "brightness_menu", 656: "macro1", 657: "macro2",
    658: "macro3", 659: "macro4", 660: "macro5", 661: "macro6", 662: "macro7", 663: "macro8", 664: "macro9",
    665: "macro10", 666: "macro11", 667: "macro12", 668: "macro13", 669: "macro14", 670: "macro15", 671:
    "macro16", 672: "macro17", 673: "macro18", 674: "macro19", 675: "macro20", 676: "macro21", 677: "macro22",
    678: "macro23", 679: "macro24", 680: "macro25", 681: "macro26", 682: "macro27", 683: "macro28", 684:
    "macro29", 685: "macro30", 688: "macro_record_start", 689: "macro_record_stop", 690: "macro_preset_cycle",
    691: "macro_preset1", 692: "macro_preset2", 693: "macro_preset3", 696: "kbd_lcd_menu1", 697:
    "kbd_lcd_menu2", 698: "kbd_lcd_menu3", 699: "kbd_lcd_menu4", 700: "kbd_lcd_menu5",
}


def name(native_scan_code: int) -> str | None:
    """The key's name from a Qt key event's nativeScanCode(): an XKB keycode, which on Linux
    is the evdev code plus 8. None off Linux, or for codes with no name."""
    return NAMES.get(native_scan_code - 8) if native_scan_code > 8 else None
