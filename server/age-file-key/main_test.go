package main

import (
	"bytes"
	"crypto/rand"
	"crypto/rsa"
	"encoding/pem"
	"os"
	"path/filepath"
	"testing"

	"filippo.io/age"
	"filippo.io/age/agessh"
	"golang.org/x/crypto/ssh"
)

type encryptedFixture struct {
	root           string
	identityPath   string
	ciphertextPath string
	keyPath        string
	ciphertext     []byte
	plaintext      []byte
}

func newEncryptedFixture(t *testing.T) encryptedFixture {
	t.Helper()
	privateKey, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	identity, err := agessh.NewRSAIdentity(privateKey)
	if err != nil {
		t.Fatal(err)
	}
	recipient := identity.Recipient()
	var ciphertext bytes.Buffer
	writer, err := age.Encrypt(&ciphertext, recipient)
	if err != nil {
		t.Fatal(err)
	}
	plaintext := []byte("historical archive fixture")
	if _, err := writer.Write(plaintext); err != nil {
		t.Fatal(err)
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	original := append([]byte(nil), ciphertext.Bytes()...)

	root := t.TempDir()
	identityPEM, err := ssh.MarshalPrivateKey(privateKey, "fixture")
	if err != nil {
		t.Fatal(err)
	}
	identityPath := filepath.Join(root, "identity")
	ciphertextPath := filepath.Join(root, "archive.age")
	if err := os.WriteFile(identityPath, pem.EncodeToMemory(identityPEM), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(ciphertextPath, original, 0o600); err != nil {
		t.Fatal(err)
	}
	var key bytes.Buffer
	if err := extract(identityPath, ciphertextPath, &key); err != nil {
		t.Fatal(err)
	}
	if key.Len() != fileKeyBytes {
		t.Fatalf("file key length = %d", key.Len())
	}
	keyPath := filepath.Join(root, "file-key")
	if err := os.WriteFile(keyPath, key.Bytes(), 0o600); err != nil {
		t.Fatal(err)
	}
	return encryptedFixture{
		root:           root,
		identityPath:   identityPath,
		ciphertextPath: ciphertextPath,
		keyPath:        keyPath,
		ciphertext:     original,
		plaintext:      plaintext,
	}
}

func TestExtractedFileKeyDecryptsWithoutChangingCiphertext(t *testing.T) {
	fixture := newEncryptedFixture(t)
	outputPath := filepath.Join(fixture.root, "plaintext")
	if err := decrypt(fixture.keyPath, fixture.ciphertextPath, outputPath); err != nil {
		t.Fatal(err)
	}
	got, err := os.ReadFile(outputPath)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(got, fixture.plaintext) {
		t.Fatalf("plaintext = %q", got)
	}
	unchanged, err := os.ReadFile(fixture.ciphertextPath)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(unchanged, fixture.ciphertext) {
		t.Fatal("ciphertext changed")
	}
}

func TestExtractRejectsWrongIdentityWithoutOutput(t *testing.T) {
	fixture := newEncryptedFixture(t)
	wrongKey, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	wrongPEM, err := ssh.MarshalPrivateKey(wrongKey, "wrong fixture")
	if err != nil {
		t.Fatal(err)
	}
	wrongPath := filepath.Join(fixture.root, "wrong-identity")
	if err := os.WriteFile(wrongPath, pem.EncodeToMemory(wrongPEM), 0o600); err != nil {
		t.Fatal(err)
	}
	var output bytes.Buffer
	if err := extract(wrongPath, fixture.ciphertextPath, &output); err == nil {
		t.Fatal("wrong identity was accepted")
	}
	if output.Len() != 0 {
		t.Fatal("failed extraction emitted key material")
	}
}

func TestDecryptRemovesOutputAfterAuthenticatedPayloadFailure(t *testing.T) {
	fixture := newEncryptedFixture(t)
	tampered := append([]byte(nil), fixture.ciphertext...)
	tampered[len(tampered)-1] ^= 1
	tamperedPath := filepath.Join(fixture.root, "tampered.age")
	if err := os.WriteFile(tamperedPath, tampered, 0o600); err != nil {
		t.Fatal(err)
	}
	outputPath := filepath.Join(fixture.root, "partial-plaintext")
	if err := decrypt(fixture.keyPath, tamperedPath, outputPath); err == nil {
		t.Fatal("tampered payload was accepted")
	}
	if _, err := os.Lstat(outputPath); !os.IsNotExist(err) {
		t.Fatalf("partial plaintext output remains: %v", err)
	}
}

func TestDecryptNeverOverwritesExistingOutput(t *testing.T) {
	fixture := newEncryptedFixture(t)
	outputPath := filepath.Join(fixture.root, "existing-output")
	sentinel := []byte("do not overwrite")
	if err := os.WriteFile(outputPath, sentinel, 0o600); err != nil {
		t.Fatal(err)
	}
	if err := decrypt(fixture.keyPath, fixture.ciphertextPath, outputPath); err == nil {
		t.Fatal("existing output was accepted")
	}
	got, err := os.ReadFile(outputPath)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(got, sentinel) {
		t.Fatal("existing output changed")
	}
}

func TestRejectsSymlinkInputs(t *testing.T) {
	fixture := newEncryptedFixture(t)
	identityLink := filepath.Join(fixture.root, "identity-link")
	ciphertextLink := filepath.Join(fixture.root, "ciphertext-link")
	keyLink := filepath.Join(fixture.root, "key-link")
	for link, target := range map[string]string{
		identityLink:   fixture.identityPath,
		ciphertextLink: fixture.ciphertextPath,
		keyLink:        fixture.keyPath,
	} {
		if err := os.Symlink(target, link); err != nil {
			t.Fatal(err)
		}
	}
	var output bytes.Buffer
	if err := extract(identityLink, fixture.ciphertextPath, &output); err == nil {
		t.Fatal("symlink identity was accepted")
	}
	if err := extract(fixture.identityPath, ciphertextLink, &output); err == nil {
		t.Fatal("symlink ciphertext was accepted")
	}
	if err := decrypt(
		keyLink,
		fixture.ciphertextPath,
		filepath.Join(fixture.root, "key-link-output"),
	); err == nil {
		t.Fatal("symlink file key was accepted")
	}
}

func TestDecryptRejectsWrongFileKeyLength(t *testing.T) {
	root := t.TempDir()
	keyPath := filepath.Join(root, "file-key")
	inputPath := filepath.Join(root, "archive.age")
	if err := os.WriteFile(keyPath, []byte("short"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(inputPath, []byte("not age"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := decrypt(keyPath, inputPath, filepath.Join(root, "out")); err == nil {
		t.Fatal("short file key was accepted")
	}
}
