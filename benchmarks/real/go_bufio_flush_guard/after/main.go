package log

import (
	"bufio"
	"os"
)

func writeLog(path, line string) error {
	f, err := os.Create(path)
	if err != nil {
		return err
	}
	defer f.Close()
	w := bufio.NewWriter(f)
	defer w.Flush()
	_, err = w.WriteString(line)
	if err != nil {
		return err
	}
	return nil
}
