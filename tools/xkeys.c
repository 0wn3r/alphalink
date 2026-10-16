/* xkeys - drive an X11 window (DOSBox-X under Xvfb) from a script on stdin.
   Commands (one per line):  key <keysym> | type <text> | sleep <sec> | shot <file.png> | down/up <keysym>
   Build: gcc -O2 -o xkeys xkeys.c -lX11 /lib/x86_64-linux-gnu/libXtst.so.6 */
#include <X11/Xlib.h>
#include <X11/keysym.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
int XTestFakeKeyEvent(Display*, unsigned int, Bool, unsigned long);
int XTestFakeMotionEvent(Display*, int, int, int, unsigned long);
int XTestFakeButtonEvent(Display*, unsigned int, Bool, unsigned long);
static Display *d;
static void press(KeySym ks, int shift) {
  KeyCode sc = XKeysymToKeycode(d, XK_Shift_L), kc = XKeysymToKeycode(d, ks);
  if (!kc) { fprintf(stderr, "no keycode for %lu\n", ks); return; }
  if (shift) XTestFakeKeyEvent(d, sc, True, 0);
  XTestFakeKeyEvent(d, kc, True, 0); XFlush(d); usleep(40000);
  XTestFakeKeyEvent(d, kc, False, 0);
  if (shift) XTestFakeKeyEvent(d, sc, False, 0);
  XFlush(d); usleep(60000);
}
int main(void) {
  char line[1024];
  d = XOpenDisplay(NULL); if (!d) { fprintf(stderr, "no display\n"); return 1; }
  while (fgets(line, sizeof line, stdin)) {
    line[strcspn(line, "\n")] = 0;
    char *arg = strchr(line, ' '); if (arg) *arg++ = 0; else arg = "";
    if (!strcmp(line, "key")) press(XStringToKeysym(arg), 0);
    else if (!strcmp(line, "skey")) press(XStringToKeysym(arg), 1);
    else if (!strcmp(line, "type")) {
      for (char *p = arg; *p; p++) {
        char s[2] = {*p, 0}; int sh = 0; KeySym ks;
        if (*p == ' ') ks = XK_space; else if (*p == '.') ks = XK_period; else if (*p == ',') ks = XK_comma;
        else if (*p == '-') ks = XK_minus; else if (*p == '\\') ks = XK_backslash; else if (*p == ':') { ks = XK_semicolon; sh = 1; }
        else if (*p == '?') { ks = XK_slash; sh = 1; } else if (*p == '!') { ks = XK_1; sh = 1; }
        else if (*p >= 'A' && *p <= 'Z') { s[0] = *p - 'A' + 'a'; ks = XStringToKeysym(s); sh = 1; }
        else ks = XStringToKeysym(s);
        press(ks, sh);
      }
    } else if (!strcmp(line, "move") || !strcmp(line, "click")) {
      int x = 0, y = 0; sscanf(arg, "%d %d", &x, &y);
      XTestFakeMotionEvent(d, -1, x, y, 0); XFlush(d); usleep(150000);
      if (line[0] == 'c') { XTestFakeButtonEvent(d, 1, True, 0); XFlush(d); usleep(80000); XTestFakeButtonEvent(d, 1, False, 0); XFlush(d); usleep(150000); }
    } else if (!strcmp(line, "sleep")) usleep((useconds_t)(atof(arg) * 1e6));
    else if (!strcmp(line, "shot")) { char cmd[1200]; snprintf(cmd, sizeof cmd, "import -window root -crop 800x600+0+0 '%s'", arg); system(cmd); }
    else if (line[0] && line[0] != '#') fprintf(stderr, "unknown: %s\n", line);
  }
  XCloseDisplay(d); return 0;
}
