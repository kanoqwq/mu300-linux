# a newer mu300-linux release (mu300-update notice, run by mu300-post at boot and every 6 hours)
[ -s /run/mu300/update-available ] && { echo; cat /run/mu300/update-available; echo; }
# packages of the system before an update that are not back yet (mu300-user-packages; "dismiss" drops the note)
[ -s /etc/mu300/user-packages/note ] && { echo; cat /etc/mu300/user-packages/note; echo; }
# the last update set files of the user aside or put some back (mu300-update, carry_report), until it was read
[ -e /etc/mu300/update-report.new ] && { echo; echo 'The last update set some of your files aside or put some back: mu300-update report'; echo; }
