package main

import (
	"database/sql"
	"fmt"
)

// transfer moves funds between accounts inside an open transaction.
// BUG: on the validation-error path, Rollback is called and then Commit
// is called on the same (already-finalized) transaction. Depending on the
// driver this either panics, returns a silent error, or corrupts state.
func transfer(tx *sql.Tx, fromID, toID int, amount float64) error {
	if amount <= 0 {
		tx.Rollback()
		// BUG: falls through to Commit — transaction already rolled back
		return fmt.Errorf("amount must be positive")
	}

	if _, err := tx.Exec("UPDATE accounts SET balance = balance - ? WHERE id = ?", amount, fromID); err != nil {
		tx.Rollback()
		tx.Commit() // BUG: Commit after Rollback
		return err
	}

	if _, err := tx.Exec("UPDATE accounts SET balance = balance + ? WHERE id = ?", amount, toID); err != nil {
		tx.Rollback()
		tx.Commit() // BUG: Commit after Rollback
		return err
	}

	return tx.Commit()
}

func main() {
	fmt.Println("tx_double_finalize_guard before fixture")
}
