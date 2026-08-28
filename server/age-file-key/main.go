// age-file-key is a deliberately small bridge to age's detached-header APIs.
// It never emits archive plaintext while extracting a file key.
package main

import (
	"errors"
	"flag"
	"fmt"
	"io"
	"os"

	"filippo.io/age"
	"filippo.io/age/agessh"
)

const fileKeyBytes = 16

func regularFile(path, label string) (*os.File, error) {
	info, err := os.Lstat(path)
	if err != nil {
		return nil, fmt.Errorf("%s is unavailable: %w", label, err)
	}
	if !info.Mode().IsRegular() {
		return nil, fmt.Errorf("%s must be one regular file", label)
	}
	file, err := os.Open(path)
	if err != nil {
		return nil, fmt.Errorf("%s cannot be opened: %w", label, err)
	}
	return file, nil
}

func extract(identityPath, inputPath string, output io.Writer) error {
	identityFile, err := regularFile(identityPath, "identity")
	if err != nil {
		return err
	}
	defer identityFile.Close()
	identityBytes, err := io.ReadAll(io.LimitReader(identityFile, 64*1024+1))
	if err != nil || len(identityBytes) > 64*1024 {
		return errors.New("identity cannot be read within its size limit")
	}
	identity, err := agessh.ParseIdentity(identityBytes)
	if err != nil {
		return errors.New("identity is not an unencrypted SSH age identity")
	}
	input, err := regularFile(inputPath, "age ciphertext")
	if err != nil {
		return err
	}
	defer input.Close()
	header, err := age.ExtractHeader(input)
	if err != nil {
		return fmt.Errorf("age header extraction failed: %w", err)
	}
	key, err := age.DecryptHeader(header, identity)
	if err != nil {
		return errors.New("age header decryption failed")
	}
	if len(key) != fileKeyBytes {
		return fmt.Errorf("age returned a file key of invalid length %d", len(key))
	}
	if _, err := output.Write(key); err != nil {
		return fmt.Errorf("file key output failed: %w", err)
	}
	return nil
}

func decrypt(keyPath, inputPath, outputPath string) error {
	keyFile, err := regularFile(keyPath, "file key")
	if err != nil {
		return err
	}
	key, err := io.ReadAll(io.LimitReader(keyFile, fileKeyBytes+1))
	keyFile.Close()
	if err != nil || len(key) != fileKeyBytes {
		return errors.New("file key must contain exactly 16 bytes")
	}
	input, err := regularFile(inputPath, "age ciphertext")
	if err != nil {
		return err
	}
	defer input.Close()
	reader, err := age.Decrypt(input, age.NewInjectedFileKeyIdentity(key))
	if err != nil {
		return errors.New("age decryption initialization failed")
	}
	output, err := os.OpenFile(outputPath, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
	if err != nil {
		return fmt.Errorf("plaintext output cannot be created: %w", err)
	}
	completed := false
	defer func() {
		output.Close()
		if !completed {
			_ = os.Remove(outputPath)
		}
	}()
	if _, err := io.Copy(output, reader); err != nil {
		return errors.New("age payload decryption failed")
	}
	if err := output.Close(); err != nil {
		return errors.New("plaintext output close failed")
	}
	completed = true
	return nil
}

func main() {
	if len(os.Args) < 2 {
		fmt.Fprintln(os.Stderr, "usage: age-file-key extract|decrypt [options]")
		os.Exit(2)
	}
	var err error
	switch os.Args[1] {
	case "extract":
		flags := flag.NewFlagSet("extract", flag.ContinueOnError)
		identity := flags.String("identity", "", "unencrypted SSH identity file")
		input := flags.String("input", "", "age ciphertext")
		if flags.Parse(os.Args[2:]) != nil || flags.NArg() != 0 || *identity == "" || *input == "" {
			err = errors.New("extract requires --identity and --input")
		} else {
			err = extract(*identity, *input, os.Stdout)
		}
	case "decrypt":
		flags := flag.NewFlagSet("decrypt", flag.ContinueOnError)
		key := flags.String("file-key", "", "raw 16-byte age file key")
		input := flags.String("input", "", "age ciphertext")
		output := flags.String("output", "", "exclusive plaintext output")
		if flags.Parse(os.Args[2:]) != nil || flags.NArg() != 0 || *key == "" || *input == "" || *output == "" {
			err = errors.New("decrypt requires --file-key, --input, and --output")
		} else {
			err = decrypt(*key, *input, *output)
		}
	default:
		err = errors.New("operation must be extract or decrypt")
	}
	if err != nil {
		fmt.Fprintf(os.Stderr, "error: %v\n", err)
		os.Exit(2)
	}
}
