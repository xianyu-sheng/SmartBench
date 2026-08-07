package oracle

import (
	"database/sql"
	"fmt"

	_ "github.com/sijms/go-ora/v2"
)

func CheckSID(ip string, port int, sid string, user string, pass string) {
	connStr := fmt.Sprintf("oracle://%s:%s@%s:%d/?sid=%s", user, pass, ip, port, sid)
	db, err := sql.Open("oracle", connStr)
	if err != nil {
		return
	}
	defer db.Close()
	_, err = db.Exec("SELECT 1 FROM DUAL")
	if err != nil {
		return
	}
}
