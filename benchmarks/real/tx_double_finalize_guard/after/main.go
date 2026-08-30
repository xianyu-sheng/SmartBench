package main

import (
	"database/sql"
	"fmt"
)

// transfer moves funds between accounts inside an open transaction.
// FIX: each error path calls exactly one finalizer (Rollback) and returns;
// the success path calls Commit. Rollback and Commit are on mutually
// exclusive paths so the double-finalize bug cannot occur.
func transfer(tx *sql.Tx, fromID, toID int, amount float64) error {
	if amount <= 0 {
		tx.Rollback()
		return fmt.Errorf("amount must be positive")
	}

	if _, err := tx.Exec("UPDATE accounts SET balance = balance - ? WHERE id = ?", amount, fromID); err != nil {
		tx.Rollback()
		return err
	}

	if _, err := tx.Exec("UPDATE accounts SET balance = balance + ? WHERE id = ?", amount, toID); err != nil {
		tx.Rollback()
		return err
	}

	return tx.Commit()
}

func main() {
	fmt.Println("tx_double_finalize_guard after fixture")
}
